"""SeismicWatch dashboard (Dash). Reads only the warehouse.

    python dashboard/app.py          then open http://127.0.0.1:8050

Callback 1: the filters -> the headline numbers, the yearly chart and the town ranking.
Callback 2: the filters + a click on a town -> the map (with that town's 50 km circle) and its event list.
The map outlines are served from dashboard/assets/topojson, so the app needs no CDN.
"""
import math
import sys
from contextlib import closing
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dash_table, dcc, html, no_update

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard import queries as q      # noqa: E402
from src import config                  # noqa: E402

COUNTRY_COLORS = {"TR": "#c0392b", "LB": "#1f6f8b", "CY": "#d68910"}
COUNTRY_NAMES = config.STUDY_COUNTRIES
GRAPH_CONFIG = {"displaylogo": False, "topojsonURL": "/assets/topojson/"}
MAP_RANGE = {"lat": [32.3, 42.7], "lon": [25.3, 45.2]}


def circle(lat, lon, radius_km, n=72):
    """Points of a circle on the map (equirectangular approximation, fine at 50 km)."""
    t = np.linspace(0, 2 * math.pi, n)
    dlat = radius_km / 111.0 * np.sin(t)
    dlon = radius_km / (111.0 * math.cos(math.radians(lat))) * np.cos(t)
    return lat + dlat, lon + dlon


def kpi(label, value, note=""):
    return html.Div(className="kpi", children=[html.Div(value, className="kpi-value"),
                                               html.Div(label, className="kpi-label"),
                                               html.Div(note, className="kpi-note")])


def empty_figure(text):
    fig = go.Figure()
    fig.add_annotation(text=text, showarrow=False, font={"size": 14, "color": "#666"})
    fig.update_layout(xaxis_visible=False, yaxis_visible=False, template="plotly_white", height=360)
    return fig


def create_app(db_path=None):
    conn = q.connect(db_path)
    b = q.bounds(conn)
    run = q.last_run(conn)
    conn.close()
    mag_max = math.ceil((b["mag_max"] or 8) * 10) / 10
    default_end = min(b["date_max"], config.STUDY_END) if b["date_max"] else config.STUDY_END

    app = Dash(__name__, title="SeismicWatch")
    app.layout = html.Div(className="page", children=[
        html.Header(children=[
            html.H1("SeismicWatch"),
            html.P("Earthquakes of magnitude 4.0 or more around Lebanon, Turkey and Cyprus since 14 November 2016: "
                   "how many, where, and which towns lie closest to them.", className="question"),
            html.P(f"Warehouse loaded by run {run['run_id']} ({run['mode']}, {run['finished_at']} UTC): "
                   f"{run['events_loaded']:,} earthquakes, {run['events_quarantined']:,} set aside."
                   if run else "No successful run yet.", className="freshness"),
        ]),
        html.Div(className="controls", children=[
            html.Div([html.Label("Countries"),
                      dcc.Checklist(id="countries", value=list(COUNTRY_NAMES), inline=True,
                                    options=[{"label": f" {n}", "value": c} for c, n in COUNTRY_NAMES.items()])]),
            html.Div([html.Label("Magnitude"),
                      dcc.RangeSlider(id="magnitude", min=4.0, max=mag_max, step=0.1, value=[4.0, mag_max],
                                      marks={m: f"{m:.0f}" for m in range(4, int(mag_max) + 1)},
                                      tooltip={"placement": "bottom"})], className="grow"),
            html.Div([html.Label("Period (UTC)"),
                      dcc.DatePickerRange(id="period", min_date_allowed=config.STUDY_START,
                                          max_date_allowed=b["date_max"], start_date=config.STUDY_START,
                                          end_date=default_end, display_format="D MMM YYYY")]),
            html.Div([html.Label("Town exposure radius"),
                      dcc.RadioItems(id="radius", value=50, inline=True,
                                     options=[{"label": " 25 km", "value": 25}, {"label": " 50 km", "value": 50}])]),
            html.Div([html.Label("Towns in the ranking"),
                      dcc.RadioItems(id="min-pop", value=10_000, inline=True,
                                     options=[{"label": " all", "value": 0}, {"label": " 10,000+", "value": 10_000},
                                              {"label": " 100,000+", "value": 100_000}])]),
        ]),
        html.Div(id="kpis", className="kpis"),
        html.Div(className="row", children=[
            html.Div(className="card wide", children=[
                html.H3("Where the earthquakes struck"),
                html.P("Each dot is one earthquake, sized and coloured by magnitude. Click a town in the ranking "
                       "to draw its exposure circle.", className="hint"),
                dcc.Graph(id="map", config=GRAPH_CONFIG)]),
            html.Div(className="card", children=[
                html.H3("Earthquakes per year"),
                dcc.Graph(id="per-year", config=GRAPH_CONFIG),
                html.H3("Documented major earthquakes", style={"marginTop": "6px"}),
                html.P("USGS events matched to Wikipedia's list (same day +/- 1, magnitude +/- 0.3).",
                       className="hint"),
                dash_table.DataTable(id="documented", page_size=6, style_table={"overflowX": "auto"},
                                     columns=[{"name": "Date (UTC)", "id": "date_utc"},
                                              {"name": "Mag.", "id": "magnitude"},
                                              {"name": "Nearest town", "id": "nearest_place"},
                                              {"name": "Wikipedia", "id": "wiki_place"},
                                              {"name": "Deaths", "id": "wiki_deaths"}],
                                     style_cell={"fontFamily": "Segoe UI, Helvetica, Arial, sans-serif",
                                                 "fontSize": 12.5, "padding": "3px", "maxWidth": 160,
                                                 "overflow": "hidden", "textOverflow": "ellipsis"},
                                     style_header={"fontWeight": 600}),
            ]),
        ]),
        html.Div(className="row", children=[
            html.Div(className="card", children=[
                html.H3("Most exposed towns"),
                html.P("Settlements with the most M4+ earthquakes within the radius (population from GeoNames). "
                       "Click a bar to see the town on the map.", className="hint"),
                dcc.Graph(id="towns", config=GRAPH_CONFIG)]),
            html.Div(className="card", children=[
                html.H3(id="town-title", children="Earthquakes near the selected town"),
                dash_table.DataTable(id="town-events", page_size=10, sort_action="native",
                                     style_table={"overflowX": "auto"},
                                     style_cell={"fontFamily": "Segoe UI, Helvetica, Arial, sans-serif",
                                                 "fontSize": 12.5, "padding": "4px", "maxWidth": 230,
                                                 "overflow": "hidden", "textOverflow": "ellipsis"},
                                     style_header={"fontWeight": 600}),
            ]),
        ]),
        dcc.Store(id="selected-town"),
        html.Footer(["Sources: USGS Earthquake Catalog (public domain), GeoNames (CC BY 4.0), Wikipedia "
                     "'List of earthquakes in Turkey' (CC BY-SA 4.0). DSC 620, Tala Mezher and Sireen Hammoud."]),
    ])

    # ---- callback 1: filters -> headline numbers, yearly chart, town ranking ------------------------
    @app.callback(Output("kpis", "children"), Output("per-year", "figure"), Output("towns", "figure"),
                  Output("documented", "data"),
                  Input("countries", "value"), Input("magnitude", "value"), Input("period", "start_date"),
                  Input("period", "end_date"), Input("radius", "value"), Input("min-pop", "value"))
    def update_summary(countries, mags, start, end, radius, min_pop):
        if not countries:
            msg = empty_figure("Pick at least one country")
            return [kpi("Earthquakes", "0")], msg, msg, []
        with closing(q.connect(db_path)) as conn:
            ev = q.earthquakes(conn, countries, mags, (start[:10], end[:10]))
            towns = q.town_ranking(conn, countries, mags, (start[:10], end[:10]), radius, min_population=min_pop)
            docs = q.documented(conn, countries, mags, (start[:10], end[:10]))
        if ev.empty:
            msg = empty_figure("No earthquake matches these filters")
            return [kpi("Earthquakes", "0")], msg, msg, []
        strongest = ev.loc[ev["magnitude"].idxmax()]
        years = ev["year"].nunique()
        kpis = [
            kpi("M4+ earthquakes", f"{len(ev):,}", f"about {len(ev) / max(years, 1):.0f} a year"),
            kpi("M5+ earthquakes", f"{(ev['magnitude'] >= 5).sum():,}", f"M6+: {(ev['magnitude'] >= 6).sum()}"),
            kpi("Strongest", f"M{strongest['magnitude']:.1f}",
                f"{strongest['event_time_utc']:%d %b %Y}, near {strongest['nearest_place']}"),
            kpi("Most exposed town", towns.iloc[0]["place_name"] if len(towns) else "-",
                f"{int(towns.iloc[0]['earthquakes'])} earthquakes within {radius} km" if len(towns) else ""),
        ]
        # every year of the period and every selected country, zeros included: a quiet year is an answer too
        years = range(int(start[:4]), int(end[:4]) + 1)
        grid = pd.MultiIndex.from_product([years, countries], names=["year", "country_code"])
        per_year = (ev.groupby(["year", "country_code"]).size().reindex(grid, fill_value=0)
                    .reset_index(name="earthquakes"))
        per_year["country"] = per_year["country_code"].map(COUNTRY_NAMES)
        per_year["year"] = per_year["year"].astype(str)
        fig_year = px.bar(per_year, x="year", y="earthquakes", color="country",
                          color_discrete_map={COUNTRY_NAMES[c]: col for c, col in COUNTRY_COLORS.items()},
                          labels={"year": "", "earthquakes": "M4+ earthquakes"}, template="plotly_white")
        fig_year.update_layout(height=330, legend_title_text="", margin={"t": 10, "l": 50, "r": 10, "b": 30},
                               xaxis={"type": "category"}, barmode="stack")
        towns = towns.sort_values("earthquakes")
        towns["label"] = towns["place_name"] + " (" + towns["place_country"] + ")"
        fig_towns = px.bar(towns, x="earthquakes", y="label", orientation="h", template="plotly_white",
                           custom_data=["place_key", "population", "max_magnitude", "closest_km", "m5_plus"],
                           color="place_country", color_discrete_map=COUNTRY_COLORS,
                           labels={"earthquakes": f"M4+ earthquakes within {radius} km", "label": ""})
        fig_towns.update_traces(hovertemplate="%{y}<br>%{x} earthquakes<br>population %{customdata[1]:,}"
                                              "<br>strongest M%{customdata[2]:.1f}, closest %{customdata[3]:.1f} km"
                                              "<br>M5+: %{customdata[4]}<extra></extra>")
        fig_towns.update_layout(height=420, showlegend=False, margin={"t": 10, "l": 10, "r": 10, "b": 40})
        return kpis, fig_year, fig_towns, docs.to_dict("records")

    # ---- remember the clicked town -----------------------------------------------------------------
    @app.callback(Output("selected-town", "data"), Input("towns", "clickData"), prevent_initial_call=True)
    def select_town(click):
        if not click:
            return no_update
        return int(click["points"][0]["customdata"][0])

    # ---- callback 2: filters + selected town -> map and event list ----------------------------------
    @app.callback(Output("map", "figure"), Output("town-events", "data"), Output("town-events", "columns"),
                  Output("town-title", "children"),
                  Input("countries", "value"), Input("magnitude", "value"), Input("period", "start_date"),
                  Input("period", "end_date"), Input("radius", "value"), Input("selected-town", "data"))
    def update_map(countries, mags, start, end, radius, town):
        countries = countries or []
        with closing(q.connect(db_path)) as conn:
            ev = q.earthquakes(conn, countries, mags, (start[:10], end[:10])) if countries else pd.DataFrame()
            near = q.events_near_place(conn, town, mags, (start[:10], end[:10]), radius) if town else None
            town_row = pd.read_sql("SELECT place_name, latitude, longitude, population FROM dim_place "
                                   "WHERE place_key = ?", conn, params=[town]) if town else None
        fig = go.Figure()
        if not ev.empty:
            fig.add_trace(go.Scattergeo(
                lat=ev["latitude"], lon=ev["longitude"], mode="markers",
                marker={"size": 4 + (ev["magnitude"] - 4.0) ** 1.6 * 5, "color": ev["magnitude"],
                        "colorscale": "YlOrRd", "cmin": 3.6, "cmax": max(6.5, ev["magnitude"].max()),
                        "line": {"width": 0.4, "color": "#555"}, "opacity": 0.8,
                        "colorbar": {"title": "Mag.", "thickness": 12, "len": 0.6}},
                text=("M" + ev["magnitude"].map("{:.1f}".format) + " | " + ev["event_time_utc"].dt.strftime("%d %b %Y")
                      + "<br>" + ev["usgs_place_text"].fillna("") + "<br>nearest town: " + ev["nearest_place"]
                      + " (" + ev["distance_to_place_km"].map("{:.0f}".format) + " km)"),
                hoverinfo="text", name="earthquakes"))
        title = "Earthquakes near the selected town"
        rows, cols = [], []
        if town_row is not None and len(town_row):
            t = town_row.iloc[0]
            clat, clon = circle(t["latitude"], t["longitude"], radius)
            fig.add_trace(go.Scattergeo(lat=clat, lon=clon, mode="lines", line={"color": "#1f6f8b", "width": 2},
                                        hoverinfo="skip", name=f"{radius} km"))
            fig.add_trace(go.Scattergeo(lat=[t["latitude"]], lon=[t["longitude"]], mode="markers+text",
                                        marker={"symbol": "star", "size": 14, "color": "#1f6f8b"},
                                        text=[t["place_name"]], textposition="top center", name="town"))
            title = f"{t['place_name']}: {len(near)} earthquakes within {radius} km (population {t['population']:,})"
            rows = near.assign(is_documented=near["is_documented"].map({1: "yes", 0: ""})).to_dict("records")
            names = {"time_utc": "Time (UTC)", "magnitude": "Mag.", "depth_km": "Depth (km)",
                     "distance_km": "Distance (km)", "epicentre_town": "Nearest town to the epicentre",
                     "is_documented": "On Wikipedia", "wiki_deaths": "Deaths", "event_id": "USGS id"}
            cols = [{"name": names[c], "id": c} for c in names]
        fig.update_geos(projection_type="mercator", resolution=50, showcountries=True, countrycolor="#999",
                        showcoastlines=True, coastlinecolor="#777", showland=True, landcolor="#f4f1ea",
                        showocean=True, oceancolor="#e6f0f5", lataxis_range=MAP_RANGE["lat"],
                        lonaxis_range=MAP_RANGE["lon"])
        fig.update_layout(height=520, margin={"t": 0, "l": 0, "r": 0, "b": 0}, showlegend=False)
        return fig, rows, cols, title

    return app


app = None
if __name__ == "__main__":
    app = create_app()
    app.run(debug=False, host="127.0.0.1", port=8050)
