from pathlib import Path
import pandas as pd


# ==================================================
# CONFIGURATION
# ==================================================

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"

SHIPMENTS_FILE = DATA_DIR / "shipment_records.csv"
NOTES_FILE = DATA_DIR / "context_notes.csv"
OUTPUT_FILE = OUTPUT_DIR / "shipment_cost_analysis.csv"

HISTORY_WEEKS = 8
FLAG_THRESHOLD = 20.0


# ==================================================
# LOAD AND VALIDATE DATA
# ==================================================

def load_data():
    shipments = pd.read_csv(SHIPMENTS_FILE)
    notes = pd.read_csv(NOTES_FILE)

    required_shipment_columns = {
        "origin",
        "destination",
        "route_type",
        "quantity_tonnes",
        "distance_km",
        "freight_cost_inr",
        "shipment_date",
    }

    required_note_columns = {
        "note_id",
        "date",
        "applies_to",
        "note",
    }

    missing_shipments = required_shipment_columns - set(shipments.columns)
    missing_notes = required_note_columns - set(notes.columns)

    if missing_shipments:
        raise ValueError(
            f"Missing shipment columns: {sorted(missing_shipments)}"
        )

    if missing_notes:
        raise ValueError(
            f"Missing context-note columns: {sorted(missing_notes)}"
        )

    shipments["shipment_date"] = pd.to_datetime(
        shipments["shipment_date"], errors="coerce"
    )

    notes["date"] = pd.to_datetime(notes["date"], errors="coerce")

    numeric_columns = [
        "quantity_tonnes",
        "distance_km",
        "freight_cost_inr",
    ]

    for column in numeric_columns:
        shipments[column] = pd.to_numeric(
            shipments[column], errors="coerce"
        )

    shipments = shipments.dropna(
        subset=[
            "origin",
            "destination",
            "route_type",
            "shipment_date",
            "quantity_tonnes",
            "distance_km",
            "freight_cost_inr",
        ]
    )

    shipments = shipments[
        (shipments["quantity_tonnes"] > 0)
        & (shipments["distance_km"] > 0)
        & (shipments["freight_cost_inr"] >= 0)
    ].copy()

    notes = notes.dropna(
        subset=["note_id", "date", "applies_to", "note"]
    ).copy()

    return shipments, notes


# ==================================================
# WEEKLY COST PER TONNE-KM
# ==================================================

def calculate_weekly_costs(shipments):
    shipments = shipments.copy()

    shipments["week_of"] = (
        shipments["shipment_date"]
        - pd.to_timedelta(
            shipments["shipment_date"].dt.weekday, unit="D"
        )
    ).dt.normalize()

    shipments["route"] = (
        shipments["origin"].astype(str).str.strip()
        + "-"
        + shipments["destination"].astype(str).str.strip()
    )

    shipments["tonne_km"] = (
        shipments["quantity_tonnes"] * shipments["distance_km"]
    )

    weekly = (
        shipments.groupby(
            ["route", "route_type", "week_of"],
            as_index=False,
        )
        .agg(
            total_freight_cost=("freight_cost_inr", "sum"),
            total_tonne_km=("tonne_km", "sum"),
        )
    )

    weekly["cost_per_tonne_km"] = (
        weekly["total_freight_cost"] / weekly["total_tonne_km"]
    )

    weekly = weekly.sort_values(
        ["route", "route_type", "week_of"]
    ).reset_index(drop=True)

    return weekly


# ==================================================
# COMPARE WITH THE ROUTE'S PREVIOUS 8 WEEKS
# ==================================================

def compare_own_history(weekly):
    weekly = weekly.copy()

    weekly["own_history_average"] = (
        weekly.groupby(["route", "route_type"])["cost_per_tonne_km"]
        .transform(
            lambda series: (
                series.shift(1)
                .rolling(HISTORY_WEEKS, min_periods=1)
                .mean()
            )
        )
    )

    weekly["vs_own_history"] = (
        (
            weekly["cost_per_tonne_km"]
            - weekly["own_history_average"]
        )
        / weekly["own_history_average"]
        * 100
    )

    return weekly


# ==================================================
# COMPARE WITH OTHER ROUTES OF THE SAME ROUTE TYPE
# ==================================================

def compare_similar_routes(weekly):
    weekly = weekly.copy()

    weekly["peer_total"] = (
        weekly.groupby(["week_of", "route_type"])["cost_per_tonne_km"]
        .transform("sum")
    )

    weekly["peer_count"] = (
        weekly.groupby(["week_of", "route_type"])["cost_per_tonne_km"]
        .transform("count")
    )

    # Exclude the current route from its peer average.
    weekly["similar_routes_average"] = (
        (weekly["peer_total"] - weekly["cost_per_tonne_km"])
        / (weekly["peer_count"] - 1)
    )

    weekly.loc[
        weekly["peer_count"] <= 1, "similar_routes_average"
    ] = float("nan")

    weekly["vs_similar_routes"] = (
        (
            weekly["cost_per_tonne_km"]
            - weekly["similar_routes_average"]
        )
        / weekly["similar_routes_average"]
        * 100
    )

    return weekly


# ==================================================
# FIND A RELEVANT CONTEXT NOTE
# ==================================================

def find_matching_note(route, week_of, notes):
    week_end = week_of + pd.Timedelta(days=6)

    candidates = notes[
        (notes["date"] >= week_of)
        & (notes["date"] <= week_end)
        & (
            notes["applies_to"].str.strip().str.casefold().isin(
                [route.casefold(), "all routes"]
            )
        )
    ].copy()

    if candidates.empty:
        return None

    # Prefer a route-specific note over an all-routes note.
    candidates["is_route_specific"] = (
        candidates["applies_to"].str.strip().str.casefold()
        == route.casefold()
    )

    candidates = candidates.sort_values(
        ["is_route_specific", "date", "note_id"],
        ascending=[False, True, True],
    )

    return candidates.iloc[0]


# ==================================================
# CHECK WHETHER THE NOTE ACTUALLY EXPLAINS A COST RISE
# ==================================================

def note_justifies_increase(note_text):
    text = str(note_text).casefold()

    negative_phrases = [
        "costs were not significantly affected",
        "without a rate change",
        "no significant disruptions",
        "no major disruptions",
        "remained normal",
        "not part of this dataset",
        "returned to normal",
        "costs weren't affected",
    ]

    if any(phrase in text for phrase in negative_phrases):
        return False

    explanation_phrases = [
        "higher trip costs",
        "higher costs",
        "pushing up transportation costs",
        "temporary surcharge",
        "increased transportation costs",
        "increased costs",
        "higher freight costs",
        "forcing longer detours",
        "cost increase",
    ]

    return any(phrase in text for phrase in explanation_phrases)


# ==================================================
# FORMAT COMPARISON VALUES
# ==================================================

def format_comparison(value, description):
    if pd.isna(value):
        return "N/A"

    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}% {description}"


# ==================================================
# GENERATE FINAL RESULTS
# ==================================================

def generate_results(weekly, notes):
    results = []

    for _, row in weekly.iterrows():
        route = row["route"]
        week_of = row["week_of"]

        own_change = row["vs_own_history"]
        peer_change = row["vs_similar_routes"]

        # A route is considered for review if either comparison
        # shows a rise of at least the configured threshold.
        own_trigger = (
            pd.notna(own_change)
            and own_change >= FLAG_THRESHOLD
        )

        peer_trigger = (
            pd.notna(peer_change)
            and peer_change >= FLAG_THRESHOLD
        )

        should_review = own_trigger or peer_trigger

        matched_note_id = ""
        reason = "No unusual cost rise detected in the available comparisons."
        flagged = "No"

        if should_review:
            note = find_matching_note(route, week_of, notes)

            if note is not None:
                note_text = str(note["note"])
                note_id = str(note["note_id"])

                if note_justifies_increase(note_text):
                    flagged = "No (justified)"
                    matched_note_id = note_id

                    reason = (
                        f"Matches note {note_id} dated "
                        f"{note['date'].date()}: {note_text}"
                    )
                else:
                    flagged = "Yes"

                    reason = (
                        f"Note {note_id} dated {note['date'].date()} "
                        f"does not establish a reason for the cost rise. "
                        "No genuine justification found; flagged for review."
                    )
            else:
                flagged = "Yes"

                reason = (
                    "No matching note found for this route or week. "
                    "Cost rise looks unexplained and requires human review."
                )

        results.append(
            {
                "route": route,
                "week_of": week_of.strftime("%Y-%m-%d"),
                "cost_per_tonne_km": round(
                    row["cost_per_tonne_km"], 2
                ),
                "vs_own_history": format_comparison(
                    own_change, "vs this route's past average"
                ),
                "vs_similar_routes": format_comparison(
                    peer_change, "vs similar-length routes this week"
                ),
                "flagged": flagged,
                "matched_note_id": matched_note_id,
                "reason": reason,
            }
        )

    return pd.DataFrame(
        results,
        columns=[
            "route",
            "week_of",
            "cost_per_tonne_km",
            "vs_own_history",
            "vs_similar_routes",
            "flagged",
            "matched_note_id",
            "reason",
        ],
    )


# ==================================================
# MAIN
# ==================================================

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading shipment and context-note data...")

    shipments, notes = load_data()

    print(f"Shipment records loaded: {len(shipments)}")
    print(f"Context notes loaded: {len(notes)}")

    print("Calculating weekly route costs...")

    weekly = calculate_weekly_costs(shipments)

    print("Comparing historical and similar-route costs...")

    weekly = compare_own_history(weekly)
    weekly = compare_similar_routes(weekly)

    print("Checking context notes and generating explanations...")

    results = generate_results(weekly, notes)

    results.to_csv(OUTPUT_FILE, index=False)

    print("\nAnalysis completed.")
    print(f"Output saved to: {OUTPUT_FILE}")
    print(f"Total weekly route records: {len(results)}")
    print(
        "Flagged for review:",
        (results["flagged"] == "Yes").sum(),
    )
    print(
        "Justified cost rises:",
        (results["flagged"] == "No (justified)").sum(),
    )

    print("\nSample results:")
    print(results.head(5).to_string(index=False))


if __name__ == "__main__":
    main()