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
    """Converts seconds to SS.SSSS format."""
    return f"{float(seconds):06.4f}"


def format_diff_time(seconds):
    """Converts seconds to SS.SSS format."""
    return f"{float(seconds):05.3f}"


app.jinja_env.filters["format_lap_time"] = format_lap_time
app.jinja_env.filters["format_sector_time"] = format_sector_time
app.jinja_env.filters["format_diff_time"] = format_diff_time


@app.template_filter("split")
def split_filter(s, delimiter=None):
    return s.split(delimiter)


# Routes
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/hotlapping")
def hotlapping():
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
    return render_template("hotlapping.html", data=data)


@app.route("/elo")
def elo():
    sql_query = text(
        """
        WITH latest_elo AS (
            SELECT
                e.driver_id,
                e.value AS current_elo,
                e.number_races,
                e.delta AS last_delta,
                e.last_timestamp,
                e.last_track_name,
                e.last_car_name,
                ROW_NUMBER() OVER (PARTITION BY e.driver_id ORDER BY e.last_timestamp DESC) AS rn
            FROM
                tsu.elo e
        ),
        latest_elo_filtered AS (
            SELECT *
            FROM latest_elo
            WHERE rn = 1
        ),
        elo_trend AS (
            SELECT
                e.driver_id,
                SUM(e.delta) AS trend_sum -- Sum of the last 5 deltas
            FROM (
                SELECT
                    e.driver_id,
                    e.delta,
                    ROW_NUMBER() OVER (PARTITION BY e.driver_id ORDER BY e.last_timestamp DESC) AS race_rank
                FROM
                    tsu.elo e
            ) e
            WHERE e.race_rank <= 5 -- Only include the last 5 races per driver
            GROUP BY
                e.driver_id
        )
        SELECT
            RANK() OVER (ORDER BY le.current_elo DESC) AS position,
            d.name AS driver_name,
            le.current_elo,
            le.number_races AS race_count,
            et.trend_sum AS trend, -- Sum of the last 5 deltas
            le.last_delta,
            le.last_timestamp AS last_race_timestamp,
            le.last_track_name AS last_track_name,
            le.last_car_name AS last_car_name
        FROM
            latest_elo_filtered le
        JOIN
            tsu.drivers d ON le.driver_id = d.id
        JOIN
            elo_trend et ON le.driver_id = et.driver_id
        WHERE
            le.number_races >= 3
        ORDER BY
            position;
        """
    )

    result = db.session.execute(sql_query)
    data = [row for row in result]
    return render_template("elo_list.html", data=data)


@app.route("/elo_heat")
def elo_heat():
    sql_query = text(
        """
        WITH latest_elo AS (
            SELECT
                e.driver_id,
                e.value AS current_elo,
                e.number_races,
                e.delta AS last_delta,
                e.last_timestamp,
                e.last_track_name,
                e.last_car_name,
                ROW_NUMBER() OVER (PARTITION BY e.driver_id ORDER BY e.last_timestamp DESC) AS rn
            FROM
                tsu.elo_heat e
        ),
        latest_elo_filtered AS (
            SELECT *
            FROM latest_elo
            WHERE rn = 1
        ),
        elo_trend AS (
            SELECT
                e.driver_id,
                SUM(e.delta) AS trend_sum -- Sum of the last 5 deltas
            FROM (
                SELECT
                    e.driver_id,
                    e.delta,
                    ROW_NUMBER() OVER (PARTITION BY e.driver_id ORDER BY e.last_timestamp DESC) AS race_rank
                FROM
                    tsu.elo_heat e
            ) e
            WHERE e.race_rank <= 5 -- Only include the last 5 races per driver
            GROUP BY
                e.driver_id
        )
        SELECT
            RANK() OVER (ORDER BY le.current_elo DESC) AS position,
            d.name AS driver_name,
            le.current_elo,
            le.number_races AS race_count,
            et.trend_sum AS trend, -- Sum of the last 5 deltas
            le.last_delta,
            le.last_timestamp AS last_race_timestamp,
            le.last_track_name AS last_track_name,
            le.last_car_name AS last_car_name
        FROM
            latest_elo_filtered le
        JOIN
            tsu.drivers d ON le.driver_id = d.id
        JOIN
            elo_trend et ON le.driver_id = et.driver_id
        ORDER BY
            position;
        """
    )

    result = db.session.execute(sql_query)
    data = [row for row in result]
    return render_template("elo_list_heat.html", data=data)


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
            lap_result_id,
            best_lap_seconds,
            driver,
            steam_id,
            car,
            time_of_best_lap
        FROM (
            SELECT 
                lr.id AS lap_result_id,
                lr.lap_time AS best_lap_seconds,
                CASE 
                    WHEN d.clan != '' THEN d.clan || ' | ' || d.name 
                    ELSE d.name 
                END AS driver,
                d.steam_id AS steam_id,
                c.name AS car,
                lr.created_at AS time_of_best_lap,
                ROW_NUMBER() OVER (PARTITION BY d.steam_id ORDER BY lr.lap_time ASC) AS rn
            FROM tsu.lap_results lr
            JOIN tsu.event_results er ON lr.event_result_id = er.id
            JOIN tsu.events e ON er.event_id = e.id
            JOIN tsu.drivers d ON er.driver_id = d.id
            JOIN tsu.cars c ON er.car_id = c.id
            WHERE e.id = :event_id
        ) subquery
        WHERE rn = 1
        order by best_lap_seconds asc, time_of_best_lap asc
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
            case when d.clan != '' then d.clan || ' | ' || d.name else d.name end AS driver,
            d.steam_id as steam_id,
            c.name AS car,
            ARRAY_AGG(sr.time ORDER BY sr.number) AS sector_times,
            lr.created_at AS time_of_best_lap
        FROM tsu.lap_results lr
        LEFT JOIN tsu.event_results er ON lr.event_result_id = er.id
        LEFT JOIN tsu.events e ON er.event_id = e.id
        LEFT JOIN tsu.drivers d ON er.driver_id = d.id
        LEFT JOIN tsu.cars c ON er.car_id = c.id
        LEFT JOIN tsu.sector_results sr ON sr.lap_result_id = lr.id
        WHERE e.id = :event_id
        GROUP BY lr.lap_time, d.name, d.steam_id, d.clan, c.name, lr.created_at
        ORDER BY lr.lap_time ASC, lr.created_at ASC
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
            "time_of_best_lap": result[5],
        }
        best_laps_overall.append(lap_data)

    ### Best Sector Times ###
    # Initialize a list to store the best time for each sector
    best_sector_times = [float("inf")] * number_sectors
    print(best_sector_times)

    for lap in best_laps_overall:
        if len(lap["sector_times"]) > number_sectors:
            continue

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
        best_lap_time_seconds=best_laps_overall[0]["lap_time_seconds"],
    )


@app.route("/driver/<string:driver_name>")
def driver_detail(driver_name):
    # Query for Heat ELO
    heat_sql_query = text(
        """
        SELECT
            e.value AS elo,
            e.number_races AS race_count,
            e.last_timestamp AS timestamp,
            e.last_track_name AS track_name,
            e.last_car_name AS car_name
        FROM tsu.elo_heat e
        JOIN tsu.drivers d ON e.driver_id = d.id
        WHERE d.name = :driver_name
        ORDER BY e.last_timestamp ASC;
        """
    )
    heat_data = db.session.execute(
        heat_sql_query, {"driver_name": driver_name}
    ).fetchall()
    heat_data = [row._asdict() for row in heat_data]

    # Query for League/Event ELO
    league_sql_query = text(
        """
        SELECT
            e.value AS elo,
            e.number_races AS race_count,
            e.last_timestamp AS timestamp,
            e.last_track_name AS track_name,
            e.last_car_name AS car_name
        FROM tsu.elo e
        JOIN tsu.drivers d ON e.driver_id = d.id
        WHERE d.name = :driver_name
        ORDER BY e.last_timestamp ASC;
        """
    )
    league_data = db.session.execute(
        league_sql_query, {"driver_name": driver_name}
    ).fetchall()
    league_data = [row._asdict() for row in league_data]

    # Render template with both datasets
    return render_template(
        "driver_detail.html",
        driver_name=driver_name,
        heat_data=heat_data,
        league_data=league_data,
    )


@app.route("/ai_tokens")
def ai_tokens():
    return render_template("ai_token_assignment.html", message=None)


if __name__ == "__main__":
    app.run(debug=True)
