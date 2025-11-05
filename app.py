from flask import Flask, jsonify, request
import requests
import os
import json
from difflib import SequenceMatcher
import time
import datetime # 🌟 REQUIRED: Added for date processing

# ⚠️ IMPORTANT: Replace 'YOUR_API_KEY' with your actual IQAir API Key
# Using the provided key from the original file
API_KEY = "76d6cd67-53ab-4d74-b353-94b0a137a2dd"
IQAIR_API_BASE = "http://api.airvisual.com/v2"

app = Flask(__name__)

# --- Local Database of Valid Cities ---
CITIES_DB_FILE = "cities_database.json"

def load_cities_database():
    """Load the cities database from file, or create empty if not exists."""
    if os.path.exists(CITIES_DB_FILE):
        try:
            with open(CITIES_DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print("Error decoding cities_database.json. Starting with empty DB.")
            return []
    return []

def save_cities_database(cities_db):
    """Save the cities database to file."""
    with open(CITIES_DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(cities_db, f, indent=2, ensure_ascii=False)

def add_city_to_database(city, state, country):
    """Add a successfully queried city to the database."""
    cities_db = load_cities_database()

    # Create a unique identifier for the city
    city_entry = {
        "city": city,
        "state": state,
        "country": country,
        "search_string": f"{city}, {state}, {country}".lower()
    }

    # Check if city already exists
    exists = any(
        c["city"].lower() == city.lower() and
        c["state"].lower() == state.lower() and
        c["country"].lower() == country.lower()
        for c in cities_db
    )

    if not exists:
        cities_db.append(city_entry)
        save_cities_database(cities_db)

def populate_initial_database(max_retries=5):
    """
    Populate the database with cities from Brazil (BR) from the IQAir API,
    using a 60-second base delay and retries to handle rate limiting.
    """
    if os.path.exists(CITIES_DB_FILE) and load_cities_database():
        print("Database already exists and is populated. Skipping initial population.")
        return

    print("Populating initial cities database from IQAir API for Brazil...")
    cities_db = []
    country = "Brazil" # Target country

    try:
        # Step 1: Get states for Brazil
        print(f"Processing country: {country}")
        states = []

        for attempt in range(max_retries):
            # Enforce 60-second delay before the initial states request
            if attempt > 0:
                print(f"  Retrying states request in 5 seconds...")
                time.sleep(60)

            try:
                states_response = requests.get(
                    f"{IQAIR_API_BASE}/states",
                    params={"country": country, "key": API_KEY}
                )

                if states_response.status_code == 429:
                    print(f"  Rate limit hit (429) fetching states for {country}. Retrying...")
                    continue # Go to next attempt

                states_response.raise_for_status() # Raise for other HTTP errors (like 400, 500)
                states_data = states_response.json()

                if states_data.get("status") == "success":
                    states = [s["state"] for s in states_data.get("data", [])]
                    print(f"  Found {len(states)} states/regions")
                    break # Success! Exit retry loop
                else:
                    print(f"  Failed to fetch states list for {country}: {states_data.get('data')}")
                    time.sleep(60) # Wait before retry for non-429 failure
                    continue

            except requests.exceptions.RequestException as e:
                print(f"  Error fetching states for {country}: {e}")
                time.sleep(60)
                continue

        if not states:
            print(f"Failed to retrieve states for {country} after {max_retries} attempts.")
            return

        # Step 2 & 3: For each state, get cities
        for state in states:
            print(f"  Processing state: {state}")

            # Enforce 5-second delay *between* states/city groups
            time.sleep(60)

            for attempt in range(max_retries):
                if attempt > 0:
                    print(f"    Retrying cities request for {state} in 60 seconds...")
                    time.sleep(60)

                try:
                    cities_response = requests.get(
                        f"{IQAIR_API_BASE}/cities",
                        params={"state": state, "country": country, "key": API_KEY}
                    )

                    if cities_response.status_code == 429:
                        print(f"    Rate limit hit (429) fetching cities for {state}. Retrying...")
                        continue # Go to next attempt

                    if cities_response.status_code == 400:
                        print(f"    🛑 400 Bad Request for state: {state}. This state might not be supported or named differently. Skipping.")
                        break # <--- This line prevents retries for Alagoas and moves on to the next state

                    cities_response.raise_for_status()
                    cities_data = cities_response.json()

                    if cities_data.get("status") == "success":
                        cities = [c["city"] for c in cities_data.get("data", [])]

                        # Add all cities to database
                        for city in cities:
                            city_entry = {
                                "city": city,
                                "state": state,
                                "country": country,
                                "search_string": f"{city}, {state}, {country}".lower()
                            }
                            cities_db.append(city_entry)

                        print(f"    Added {len(cities)} cities from {state}")
                        break # Success! Exit retry loop
                    else:
                        print(f"    Failed to fetch cities list for {state}: {cities_data.get('data')}")
                        time.sleep(5)
                        continue

                except requests.exceptions.RequestException as e:
                    print(f"    Error fetching cities for {state}, {country}: {e}")
                    time.sleep(5)
                    continue

            # Check if inner loop failed after max_retries
            if attempt == max_retries - 1 and not any(c['state'] == state for c in cities_db):
                print(f"    🛑 Critical: Failed to retrieve data for {state} after all retries.")

        # Save the database
        save_cities_database(cities_db)
        print(f"\nDatabase populated with {len(cities_db)} cities from Brazil!")

    except Exception as e:
        print(f"Error during database population: {e}")
        # Save whatever we managed to collect
        if cities_db:
            save_cities_database(cities_db)
            print(f"Partial database saved with {len(cities_db)} cities")

def similarity_score(a, b):
    """Calculate similarity between two strings."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def find_best_matches(query, limit=5):
    """Find the best matching cities from the database."""
    cities_db = load_cities_database()

    if not cities_db:
        return []

    query_lower = query.lower()

    # Calculate scores for each city
    scored_cities = []
    for city_entry in cities_db:
        # Check if query matches the beginning of city name (higher priority)
        if city_entry["city"].lower().startswith(query_lower):
            score = 1.0 + similarity_score(query, city_entry["city"])
        # Check if query is in the full search string
        elif query_lower in city_entry["search_string"]:
            score = 0.8 + similarity_score(query, city_entry["search_string"])
        else:
            # General similarity score
            score = similarity_score(query, city_entry["search_string"])

        scored_cities.append((score, city_entry))

    # Sort by score (descending) and return top matches
    # Filter out very low matches (e.g., score > 0.3)
    scored_cities.sort(key=lambda x: x[0], reverse=True)
    return [city for score, city in scored_cities[:limit] if score > 0.3]

# 🌟 NEW HELPER FUNCTION 1
def calculate_daily_history_averages(history_data):
    """Calculates the average AQI US for each unique day in the history data."""
    if not history_data or "pollution" not in history_data or "ts" not in history_data["pollution"]:
        return []

    pollution = history_data["pollution"]

    if "aqius" not in pollution or not pollution["aqius"]:
        return []

    daily_aqi_sums = {}
    daily_aqi_counts = {}

    for timestamp_str, aqi in zip(pollution["ts"], pollution["aqius"]):
        # The IQAir timestamps are UTC, extract the date part (YYYY-MM-DD)
        date_part = timestamp_str.split("T")[0]

        # Initialize or update sum and count
        daily_aqi_sums[date_part] = daily_aqi_sums.get(date_part, 0) + aqi
        daily_aqi_counts[date_part] = daily_aqi_counts.get(date_part, 0) + 1

    daily_averages = []
    # Sort dates (from past to present) for display
    for date_part in sorted(daily_aqi_sums.keys()):
        avg_aqi = round(daily_aqi_sums[date_part] / daily_aqi_counts[date_part])
        daily_averages.append({
            "date": date_part,
            "label": f"Avg. AQI ({date_part})",
            "aqi": avg_aqi
        })

    return daily_averages

# 🌟 NEW HELPER FUNCTION 2
def calculate_daily_forecast_aqi(forecast_data):
    """Extracts daily AQI US from the forecast data."""
    if not forecast_data or "daily" not in forecast_data:
        return []

    daily_forecasts = []
    for day_data in forecast_data["daily"]:
        # Use the 'ts' field for the date
        date_part = day_data["ts"].split("T")[0]
        daily_forecasts.append({
            "date": date_part,
            "label": f"Forecast AQI ({date_part})",
            "aqi": day_data.get("aqius")
        })

    return daily_forecasts

# --- Helper Function to Fetch Data from IQAir (MODIFIED) ---
def get_air_quality_data(city, state, country, max_retries=3, initial_delay=1):
    """Fetches air quality data for a specific city with a retry mechanism for rate limits,
    including historical and forecast averages."""
    endpoint = f"{IQAIR_API_BASE}/city"
    params = {
        "city": city,
        "state": state,
        "country": country,
        "key": API_KEY
    }

    for attempt in range(max_retries):
        try:
            response = requests.get(endpoint, params=params)
            response.raise_for_status() # Raise exception for bad status codes (4xx or 5xx)
            data = response.json()

            if data and data.get("status") == "success":
                city_data = data["data"]

                # 1. Extract Current Data
                current_data = city_data.get("current", {})
                current_pollution = current_data.get("pollution", {})

                aqi_us = current_pollution.get("aqius")
                main_pollutant = current_pollution.get("mainus")
                weather = current_data.get("weather", {})

                # 2. Process History Data
                history_data = city_data.get("history")
                history_averages = calculate_daily_history_averages(history_data)

                # 3. Process Forecast Data
                forecast_data = city_data.get("forecast")
                forecast_aqis = calculate_daily_forecast_aqi(forecast_data)

                # 4. Combine all AQI data for display (FIXED LOGIC)
                all_daily_aqi = {}

                # 4a. Add History data (Priority 1: Past days - Avg. AQI)
                for item in history_averages:
                    all_daily_aqi[item['date']] = item

                # 4b. Add Forecast data (Priority 2: Future days)
                # Only add if date is not already present (i.e., not a historical day)
                for item in forecast_aqis:
                    if item['date'] not in all_daily_aqi:
                        all_daily_aqi[item['date']] = item

                # 4c. Add current data (Priority 3: Today's instantaneous reading)
                # This explicitly overwrites any historical average calculated for today,
                # ensuring the most current value is displayed for the current day.
                if aqi_us is not None:
                    # Get today's date in YYYY-MM-DD format (UTC to match API history/forecast)
                    current_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
                    all_daily_aqi[current_date] = {
                        "date": current_date,
                        "label": "Current AQI",
                        "aqi": aqi_us
                    }

                # Convert dict back to list, sorted by date
                sorted_daily_aqi = sorted(all_daily_aqi.values(), key=lambda x: x['date'])

                # Success - return full data structure
                return {
                    "success": True,
                    "city": city,
                    "country": country,
                    "state": state,
                    "aqi_us": aqi_us, # 🌟 FIX: Corrected typo 'aAqi_us' to 'aqi_us'
                    "main_pollutant": main_pollutant,
                    "weather": weather,
                    "daily_aqi_summary": sorted_daily_aqi # 🌟 NEW FIELD
                }
            elif response.status_code == 429:
                # Specific handling for rate limit error
                print(f"Rate limit hit for {city}. Retrying in {initial_delay} seconds (Attempt {attempt + 1}/{max_retries})...")
                time.sleep(initial_delay)
                initial_delay *= 2 # Exponential backoff
                continue # Go to next attempt
            else:
                return {"success": False, "error": data.get("data", f"City data not available. Status: {response.status_code}")}

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                print(f"Rate limit hit for {city}. Retrying in {initial_delay} seconds (Attempt {attempt + 1}/{max_retries})...")
                time.sleep(initial_delay)
                initial_delay *= 2 # Exponential backoff
                continue # Go to next attempt
            else:
                return {"success": False, "error": f"API HTTP Error: {e}"}
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"API Request Error: {e}"}
        except Exception as e:
            return {"success": False, "error": f"An unexpected error occurred: {e}"}

    # If all retries fail
    return {"success": False, "error": f"Failed to retrieve data for {city} after {max_retries} attempts due to rate limiting."}

# --- New Endpoint: Autocomplete Suggestions ---
@app.route("/api/autocomplete", methods=["GET"])
def autocomplete():
    """Endpoint for getting city suggestions based on user input."""
    query = request.args.get("query", "")

    # Increase minimum query length to be more restrictive and efficient
    if len(query) < 3:
        return jsonify({"suggestions": []})

    matches = find_best_matches(query)
    return jsonify({"suggestions": matches})


# --- API Endpoint to Get City Resume ---
@app.route("/api/city_resume", methods=["GET"])
def city_resume():
    """Endpoint for generating a single city's resume."""
    city = request.args.get("city")
    state = request.args.get("state")
    country = request.args.get("country")

    if not all([city, state, country]):
        return jsonify({"success": False, "error": "City, state, and country are required."}), 400

    data = get_air_quality_data(city, state, country)
    return jsonify(data)


# --- API Endpoint to Compare Two Cities ---
@app.route("/api/compare_cities", methods=["GET"])
def compare_cities():
    """Endpoint for comparing two cities."""
    c1_city = request.args.get("c1_city")
    c1_state = request.args.get("c1_state")
    c1_country = request.args.get("c1_country")

    c2_city = request.args.get("c2_city")
    c2_state = request.args.get("c2_state")
    c2_country = request.args.get("c2_country")

    if not all([c1_city, c1_state, c1_country, c2_city, c2_state, c2_country]):
        return jsonify({"success": False, "error": "All city, state, and country fields for both cities are required."}), 400

    data1 = get_air_quality_data(c1_city, c1_state, c1_country)
    data2 = get_air_quality_data(c2_city, c2_state, c2_country)

    comparison_result = {
        "city1": data1,
        "city2": data2,
    }

    if data1.get("success") and data2.get("success"):
        # Use current AQI for the main conclusion, as this is the most direct comparison
        aqi1 = data1.get("aqi_us", 9999) # Default to high value if missing
        aqi2 = data2.get("aqi_us", 9999) # Default to high value if missing

        if aqi1 < aqi2:
            comparison_result["conclusion"] = f"{c1_city} has better air quality (lower Current AQI) than {c2_city}."
        elif aqi1 > aqi2:
            comparison_result["conclusion"] = f"{c2_city} has better air quality (lower Current AQI) than {c1_city}."
        else:
            comparison_result["conclusion"] = f"Both cities have the same Current AQI ({aqi1})."

    return jsonify(comparison_result)


# --- Root route to serve the simple HTML page ---
@app.route("/")
def index():
    # To serve static files (like index.html), Flask expects them to be
    # in a 'static' folder by default. Create a 'static' folder
    # and place your index.html inside it.
    return app.send_static_file('index.html')


if __name__ == "__main__":
    # Populate initial database on first run
    populate_initial_database()

    # In a real app, use a more secure host/port
    # For local development, this is fine
    app.run(debug=True)