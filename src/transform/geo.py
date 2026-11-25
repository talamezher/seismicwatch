"""Distances on the Earth's surface, done with numpy (no GIS library needed).

The haversine formula gives the great-circle distance between two points; with a
mean Earth radius of 6,371 km it is within about 0.5% of the true distance,
which is plenty for "within 25 km" and "within 50 km".
"""
import numpy as np
import pandas as pd

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1, lon1, lat2, lon2):
    """Broadcasts: scalars, 1-D arrays, or a column against a row to get a matrix."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def nearest_and_within(events: pd.DataFrame, places: pd.DataFrame, radius_km: float, chunk: int = 250):
    """For every event: its nearest place, and every place within radius_km.

    events needs event_id, latitude, longitude; places needs place_id, latitude, longitude.
    Returns (nearest: one row per event, pairs: one row per event and place within the radius).
    The events are processed in chunks so the distance matrix stays small in memory.
    """
    p_lat = places["latitude"].to_numpy(float)
    p_lon = places["longitude"].to_numpy(float)
    p_id = places["place_id"].to_numpy()
    ev_id = events["event_id"].to_numpy()
    e_lat = events["latitude"].to_numpy(float)
    e_lon = events["longitude"].to_numpy(float)
    nearest_rows, pair_frames = [], []
    for start in range(0, len(events), chunk):
        sl = slice(start, start + chunk)
        d = haversine_km(e_lat[sl, None], e_lon[sl, None], p_lat[None, :], p_lon[None, :])
        j = d.argmin(axis=1)
        nearest_rows.append(pd.DataFrame({"event_id": ev_id[sl], "nearest_place_id": p_id[j],
                                          "distance_km": d[np.arange(len(j)), j]}))
        ei, pj = np.nonzero(d <= radius_km)
        pair_frames.append(pd.DataFrame({"event_id": ev_id[sl][ei], "place_id": p_id[pj],
                                         "distance_km": d[ei, pj]}))
    nearest = pd.concat(nearest_rows, ignore_index=True)
    pairs = pd.concat(pair_frames, ignore_index=True)
    nearest["distance_km"] = nearest["distance_km"].round(2)
    pairs["distance_km"] = pairs["distance_km"].round(2)
    return nearest, pairs
