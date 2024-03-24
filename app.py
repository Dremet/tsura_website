import os
import math
from flask import Flask, render_template, request, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from dotenv import load_dotenv
from collections import defaultdict

app = Flask(__name__)

# DB
load_dotenv()
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("TSU_HOTLAPPING_POSTGRES_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


def format_lap_time(seconds):
    """Converts seconds to MM:SS.SSS format."""
    minutes = int(seconds // 60)
    remaining_seconds = seconds % 60
    return f"{minutes}:{remaining_seconds:06.3f}"


def format_sector_time(seconds):
    """Converts seconds to SS.SSS format."""
    return f"{float(seconds):06.4f}"


app.jinja_env.filters["format_lap_time"] = format_lap_time
app.jinja_env.filters["format_sector_time"] = format_sector_time


@app.template_filter("split")
def split_filter(s, delimiter=None):
    return s.split(delimiter)


# Routes
@app.route("/")
def home():
    sql_query = text(
        """
        SELECT 
            e.id,
            t.name as track_name,
            STRING_AGG(c.name, ', ') as car_names,
            e.created_at as first_entry
        FROM tsu.events e
        LEFT JOIN tsu.tracks t on e.track_id = t.id
        LEFT JOIN tsu.event_car_association ec on e.id = ec.event_id
        LEFT JOIN tsu.cars c on ec.car_id = c.id
        group by e.id, t.name, e.created_at
        order by e.created_at desc
        ;
    """
    )
    result = db.session.execute(sql_query)
    # col_names = result.keys()
    data = [row for row in result]
    return render_template("index.html", data=data)


@app.route("/event/<int:event_id>")
def event(event_id):
    ### EVENT DETAILS ###
    event_sql_query = text(
        """
        SELECT 
            e.id,
            t.name as track_name,
            STRING_AGG(c.name, ', ') as car_names,
            e.created_at as first_entry
        FROM tsu.events e
        LEFT JOIN tsu.tracks t on e.track_id = t.id
        LEFT JOIN tsu.event_car_association ec on e.id = ec.event_id
        LEFT JOIN tsu.cars c on ec.car_id = c.id
        WHERE e.id = :event_id
        GROUP BY e.id, t.name, e.created_at
        ORDER BY e.created_at;
        """
    )
    event_data = (
        db.session.execute(event_sql_query, {"event_id": event_id}).mappings().first()
    )

    if not event_data:
        abort(404)

    ### RESULTS ###
    results_sql_query = text(
        """
        SELECT 
            lr.id AS lap_result_id,
            MIN(lr.lap_time) AS best_lap_seconds,
            d.clan || ' | ' || d.name AS driver,
            d.steam_id as steam_id,
            c.name AS car
        FROM tsu.lap_results lr
        LEFT JOIN tsu.event_results er ON lr.event_result_id = er.id
        LEFT JOIN tsu.events e ON er.event_id = e.id
        LEFT JOIN tsu.drivers d ON er.driver_id = d.id
        LEFT JOIN tsu.cars c ON er.car_id = c.id
        WHERE e.id = :event_id
        GROUP BY lr.id, d.name, d.steam_id , d.clan, c.name
        ORDER BY MIN(lr.lap_time) ASC
        """
    )
    results = (
        db.session.execute(results_sql_query, {"event_id": event_id}).mappings().all()
    )

    if not results:
        abort(404)

    # Group data by driver (regardless of the car)
    driver_laps = defaultdict(list)
    for row in results:
        driver = row["driver"]
        driver_laps[driver].append(row)

    # Find the best lap per driver
    best_laps_per_driver = {}
    for driver, laps in driver_laps.items():
        best_lap = min(laps, key=lambda x: x["best_lap_seconds"])
        best_laps_per_driver[driver] = best_lap

    number_sectors = 0

    # Query sector times for the best lap
    for driver, best_lap in best_laps_per_driver.items():
        lap_result_id = best_lap["lap_result_id"]
        sector_times_query = text(
            """
        SELECT sr.time, sr.number
        FROM tsu.sector_results sr
        WHERE sr.lap_result_id = :lap_result_id
        ORDER BY sr.number ASC
        """
        )
        sector_times = db.session.execute(
            sector_times_query, {"lap_result_id": lap_result_id}
        ).fetchall()
        # Konvertiere best_lap in ein reguläres Dictionary, falls noch nicht geschehen
        best_lap_dict = dict(best_lap)
        # Füge die Sektorzeiten zum best_lap_dict Dictionary hinzu
        best_lap_dict["sector_times"] = [str(sr.time) for sr in sector_times]

        number_sectors = (
            len(best_lap_dict["sector_times"])
            if len(best_lap_dict["sector_times"]) > number_sectors
            else number_sectors
        )

        # Aktualisiere das best_laps_per_driver Dictionary
        best_laps_per_driver[driver] = best_lap_dict

    ### TOP 500 LAP TIMES ###
    top_laps_sql_query = text(
        """
        SELECT 
            lr.lap_time AS lap_time_seconds,
            d.clan || ' | ' || d.name AS driver,
            d.steam_id as steam_id,
            c.name AS car,
            ARRAY_AGG(sr.time ORDER BY sr.number) AS sector_times
        FROM tsu.lap_results lr
        LEFT JOIN tsu.event_results er ON lr.event_result_id = er.id
        LEFT JOIN tsu.events e ON er.event_id = e.id
        LEFT JOIN tsu.drivers d ON er.driver_id = d.id
        LEFT JOIN tsu.cars c ON er.car_id = c.id
        LEFT JOIN tsu.sector_results sr ON sr.lap_result_id = lr.id
        WHERE e.id = :event_id
        GROUP BY lr.lap_time, d.name, d.steam_id, d.clan, c.name
        ORDER BY lr.lap_time ASC
        LIMIT 500
        """
    )
    top_laps_results = db.session.execute(
        top_laps_sql_query, {"event_id": event_id}
    ).fetchall()

    best_laps_overall = []
    for result in top_laps_results:
        lap_data = {
            "driver": result[1],
            "steam_id": result[2],
            "car": result[3],
            "lap_time_seconds": result[0],
            "sector_times": [float(time) if time else "N/A" for time in result[4]],
        }
        best_laps_overall.append(lap_data)

    ### Best Sector Times ###
    # Initialize a list to store the best time for each sector
    best_sector_times = [float("inf")] * number_sectors

    for lap in best_laps_overall:
        for index, sector_time in enumerate(lap["sector_times"]):
            if sector_time != "N/A":
                sector_time_float = float(sector_time)
                if sector_time_float < best_sector_times[index]:
                    best_sector_times[index] = sector_time_float

    sum_best_sector_times = sum(best_sector_times)

    return render_template(
        "event_detail.html",
        event=event_data,
        number_sectors=number_sectors,
        best_laps_per_driver=best_laps_per_driver,
        best_laps_overall=best_laps_overall,
        best_sector_times=best_sector_times,
        best_theoretical_lap=sum_best_sector_times,
    )


if __name__ == "__main__":
    app.run(debug=True)
