# FreightTiger Shipment Route Cost Analysis

## Overview

This project is a Python data-processing pipeline for reviewing freight costs across shipment routes. It validates shipment records, aggregates them into weekly route costs, compares each weekly cost with the route's own recent history and with other routes of the same type in that week, and adds context-note-based explanations to cost increases that cross configured thresholds.

The pipeline uses pandas and writes two CSV files under `output/`: a weekly aggregation and a route-cost analysis.

## Problem statement and objectives

Freight costs can change over time because of route-specific events or broader operating conditions. Reviewing shipment-level records alone can make it difficult to identify meaningful changes and understand whether a cost increase has a plausible explanation.

The project aims to:

- Validate required shipment fields and exclude invalid records.
- Calculate weekly freight cost per tonne-kilometre for each route.
- Compare weekly costs against recent route history and same-week costs for other routes with the same route type.
- Match route-specific or all-route context notes and produce a review flag with a human-readable reason.

## Project structure

```text
FreightTiger-CaseStudy/
├── README.md
├── main.py
├── data/
│   ├── shipment_records.csv
│   ├── context_notes.csv
│   └── sample_output_format_v2.csv
└── output/
    ├── weekly_route_costs.csv
    └── route_cost_analysis.csv
```

The script reads the shipment and context-note files from `data/` and creates `output/` if needed. `data/sample_output_format_v2.csv` is a reference file; the script does not read it. Root-level copies of some CSVs are also present in the project, but `main.py` uses the files in `data/`.

## Input files

### `data/shipment_records.csv`

Shipment-level input data. The script requires values for:

| Field | Purpose |
|---|---|
| `origin` | Shipment origin city |
| `destination` | Shipment destination city |
| `route_type` | Route category used to group and compare routes |
| `shipment_date` | Date used to assign a shipment to a week |
| `quantity_tonnes` | Shipment quantity in tonnes |
| `distance_km` | Shipment distance in kilometres |
| `freight_cost` | Shipment freight cost; the provided input names this `freight_cost_inr` |

Column names are normalized, and the script recognizes several aliases for required fields. It also reads other columns in the file but does not use fields such as shipment ID, material, or transporter in the calculations.

### `data/context_notes.csv`

Contextual notes used to explain potential cost changes. Required fields are a note identifier, date, applicability scope, and note text. In the provided file these are `note_id`, `date`, `applies_to`, and `note`. The applicability can be a route name or a general scope such as `All Routes`.

### `data/sample_output_format_v2.csv`

A sample/reference output format. It is not an input to the processing pipeline and is not used to generate either output file.

## Processing workflow and cost calculation

Run `main.py` from the project root. The pipeline performs these steps:

1. Checks that the shipment and context-note input files exist, loads them as CSVs, and normalizes column names.
2. Maps recognized input-column aliases to the required shipment fields.
3. Parses dates and numeric values, then removes shipment rows with missing or empty required route fields, invalid dates or numbers, non-positive quantity or distance, or negative freight cost.
4. Builds a route label as `origin-destination`, assigns each shipment to a week beginning Monday, and calculates shipment tonne-kilometres:

   ```text
   shipment tonne-km = quantity_tonnes × distance_km
   ```

5. Groups valid shipments by origin, destination, route type, route, and week. For each group it sums freight cost, quantity, and tonne-kilometres, and counts shipments.
6. Calculates the weekly route cost:

   ```text
   cost_per_tonne_km = total_freight_cost ÷ total_tonne_km
   ```

   This is the ratio of the group's summed freight cost to its summed tonne-kilometres, rather than an average of individual shipment rates.
7. Adds historical and same-week route comparisons, matches a context note, assesses whether the row should be flagged, and writes both output CSVs.

## Cost comparison logic

### Own-route history

Within each `route` and `route_type`, the script compares a week's cost per tonne-kilometre with the mean of up to the previous **8 weekly observations**. The current observation is excluded from its baseline. At least **4 prior observations** are required before a historical comparison is available. The history uses prior records in the route's data, not a check that all eight calendar weeks are consecutive.

### Other routes in the same week

For a given week and `route_type`, the script compares the route's cost with the unweighted mean cost per tonne-kilometre of all other routes of that same type in that week. The route itself is excluded. If there are no other routes in that group, this comparison is unavailable.

Despite the output label `vs_similar_routes`, the code does not compare route distance or length; “similar” here means the same route type and week.

## Context-note matching and flagging

Two configured thresholds are used in `main.py`:

| Comparison | Threshold |
|---|---:|
| Above own-route historical average | 20% |
| Above same-week other-route average | 20% |

A comparison triggers when its percentage increase is **at least 20%**. A row is considered elevated if either available comparison triggers.

For each route-week, the script finds notes whose `applies_to` value matches the route or a general value (`All Routes`, `all`, or `*`). If any route-specific notes exist, it considers those instead of the general notes. It selects the applicable note whose date is closest to the week start; there is no maximum date window, so a note can be selected even if it is not close in time.

An elevated cost is marked `No (justified)` only when the selected note contains at least one configured cost-driver term: `festival`, `surcharge`, `toll`, `diesel prices`, `fuel prices`, `maintenance`, `flooding`, `disruption`, or `compliance costs`. A note describing stable, steady, or unchanged demand does not justify an increase on its own. If the cost is elevated but the note does not pass this text check, the row remains flagged `Yes`. Rows that do not cross either threshold are marked `No`.

## Output files

### `output/weekly_route_costs.csv`

One row per route and week, before comparison and context-note assessment.

| Column | Meaning |
|---|---|
| `origin`, `destination` | Route endpoints |
| `route_type` | Route category |
| `route` | Combined `origin-destination` label |
| `week_of` | Start date of the week (Monday) |
| `total_freight_cost` | Sum of freight costs for the group |
| `total_quantity_tonnes` | Sum of shipment quantities in tonnes |
| `total_tonne_km` | Sum of shipment quantity multiplied by distance |
| `shipment_count` | Number of shipment records in the group |
| `cost_per_tonne_km` | Total freight cost divided by total tonne-kilometres |

### `output/route_cost_analysis.csv`

One row per route and week, with the cost comparisons and review assessment.

| Column | Meaning |
|---|---|
| `route` | Route label |
| `week_of` | Week start date, formatted `YYYY-MM-DD` |
| `cost_per_tonne_km` | Weekly cost per tonne-kilometre, rounded to two decimal places |
| `vs_own_history` | Percentage change from the historical baseline, or `N/A` if unavailable |
| `vs_similar_routes` | Percentage change from the same-week, same-route-type baseline, or `N/A` if unavailable |
| `flagged` | `Yes` for an elevated unexplained cost, `No (justified)` for an elevated cost explained by a qualifying note, or `No` when neither threshold is crossed |
| `matched_note_id` | ID of the selected note, or blank if no applicable note was found |
| `reason` | Text explaining the comparison result and any note-based assessment |

The percentage comparison fields are formatted as signed percentages with one decimal place when available.

## Prerequisites and installation

- Python installed and available as `python` in the terminal.
- pandas.

Install pandas if it is not already available:

```bash
python -m pip install pandas
```

## Run the project

From the project root directory, run exactly:

```bash
python main.py
```

The script prints progress and summary information to the terminal and writes or overwrites the two output CSV files in `output/`.

## Validation and limitations

- The script checks for missing input files, an empty shipment dataset, missing required columns, and rows with invalid required values. It removes invalid shipment rows and reports how many were removed. If no valid shipment records remain, processing stops with an error.
- Date and numeric parsing use coercion; malformed values therefore become invalid and their shipment rows are removed. Context notes with missing required values or unparseable dates are dropped.
- Historical comparisons require four prior weekly observations. When the required history or a same-week comparison group is unavailable, the corresponding percentage is reported as `N/A`.
- Context justification is based on a fixed list of text terms, not semantic interpretation or manual review. A selected note may be distant in date, and a keyword match does not prove that the note caused the cost change.
- The same-week baseline is grouped by route type, not by distance, and is an unweighted average of route-level rates.
- The output is intended to support review, not to establish causation or replace human validation. The script does not use the sample output-format CSV as a schema check.
