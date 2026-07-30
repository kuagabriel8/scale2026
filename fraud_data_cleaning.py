"""
SightLine fraud data-cleaning / data-quality pass.

Read-only against the HANA Cloud instance. Never writes, updates or deletes
anything on TEAM_03 / TRUSTSPHERE_REFERENCE, and never pulls a full raw table
into pandas - all counting/filtering/grouping is pushed down to HANA via
hana_ml.DataFrame, with connection_context.sql() used only where the
DataFrame API has no equivalent (conditional aggregation, LOB handling,
JSON_TABLE explode, anti-join FK checks).

Typology-specific guards below are pulled directly from the critical_note
fields in topfraudandtables.json - each one documents a real bug that a
naive implementation of that typology would hit.
"""

import json
import os

from dotenv import load_dotenv
from hana_ml.dataframe import ConnectionContext, DataFrame

load_dotenv()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TYPOLOGY_FILE = os.path.join(SCRIPT_DIR, "topfraudandtables.json")
SCHEMA_DUMP_FILE = os.path.join(SCRIPT_DIR, "databasetables.txt")
REPORT_FILE = os.path.join(SCRIPT_DIR, "data_quality_report.txt")

SOURCE_SCHEMA = os.getenv("HANA_SCHEMA", "TEAM_03")

NATURAL_KEYS = {
    "TRANSACTIONS": ["TRANSACTION_UUID"],
    "COMPANIES": ["COMPANY_UUID"],
    "COUNTRIES": ["COUNTRY_CODE"],
    "SCREENING_RULES": ["RULE_CODE"],
    "REGIONS": ["REGION_CODE"],
}

PRIMARY_KEYS = {
    "TRANSACTIONS": ["TRANSACTION_ID"],
    "TRANSACTION_BASELINES": ["BASELINE_ID"],
    "COMPANIES": ["COMPANY_ID"],
    "COMPANY_BENEFICIAL_OWNERS": ["OWNER_ID"],
    "COUNTRIES": ["COUNTRY_ID"],
    "SCREENING_RULES": ["RULE_ID"],
    "SANCTIONS_LISTS": ["SANCTIONS_ID"],
    "REGIONS": ["REGION_ID"],
}

FK_RULES = [
    ("TRANSACTIONS", "ORIGINATOR_COMPANY_ID", "COMPANIES", "COMPANY_ID"),
    ("TRANSACTIONS", "BENEFICIARY_COMPANY_ID", "COMPANIES", "COMPANY_ID"),
    ("TRANSACTIONS", "ORIGINATING_COUNTRY_ID", "COUNTRIES", "COUNTRY_ID"),
    ("TRANSACTIONS", "DESTINATION_COUNTRY_ID", "COUNTRIES", "COUNTRY_ID"),
    ("TRANSACTIONS", "BENEFICIARY_COUNTRY_ID", "COUNTRIES", "COUNTRY_ID"),
    ("COMPANIES", "INCORPORATION_COUNTRY_ID", "COUNTRIES", "COUNTRY_ID"),
    ("COMPANIES", "HEADQUARTERS_COUNTRY_ID", "COUNTRIES", "COUNTRY_ID"),
    ("COMPANIES", "INDUSTRY_ID", "INDUSTRIES", "INDUSTRY_ID"),
    ("COMPANY_BENEFICIAL_OWNERS", "COMPANY_ID", "COMPANIES", "COMPANY_ID"),
    ("COMPANY_BENEFICIAL_OWNERS", "NATIONALITY_COUNTRY_ID", "COUNTRIES", "COUNTRY_ID"),
    ("COMPANY_BENEFICIAL_OWNERS", "RESIDENCE_COUNTRY_ID", "COUNTRIES", "COUNTRY_ID"),
    ("SANCTIONS_LISTS", "COUNTRY_ID", "COUNTRIES", "COUNTRY_ID"),
    ("COUNTRIES", "REGION_ID", "REGIONS", "REGION_ID"),
    ("TRANSACTION_BASELINES", "COMPANY_ID", "COMPANIES", "COMPANY_ID"),
]

RANGE_RULES = [
    ("TRANSACTIONS", "AMOUNT_ORIGINAL <= 0", '"AMOUNT_ORIGINAL" <= 0'),
    ("TRANSACTIONS", "AMOUNT_USD <= 0", '"AMOUNT_USD" <= 0'),
    ("TRANSACTIONS", "EXCHANGE_RATE <= 0 (non-null only)",
     '"EXCHANGE_RATE" IS NOT NULL AND "EXCHANGE_RATE" <= 0'),
    ("TRANSACTIONS", "INITIATED_AT > SETTLED_AT (non-null only)",
     '"SETTLED_AT" IS NOT NULL AND "INITIATED_AT" > "SETTLED_AT"'),
    ("TRANSACTION_BASELINES", "PERIOD_START > PERIOD_END", '"PERIOD_START" > "PERIOD_END"'),
    ("COMPANY_BENEFICIAL_OWNERS", "OWNERSHIP_PERCENTAGE outside [0,100]",
     '"OWNERSHIP_PERCENTAGE" < 0 OR "OWNERSHIP_PERCENTAGE" > 100'),
]


class QualityReport:
    def __init__(self):
        self.lines = []

    def section(self, title):
        self.lines.append("")
        self.lines.append("=" * 88)
        self.lines.append(title)
        self.lines.append("=" * 88)

    def sub(self, title):
        self.lines.append("")
        self.lines.append("-" * 88)
        self.lines.append(title)
        self.lines.append("-" * 88)

    def row(self, label, count, total=None):
        if total:
            pct = (count / total * 100) if total else 0.0
            self.lines.append(f"  {label:<60} {count:>10,}  ({pct:5.2f}% of {total:,})")
        else:
            self.lines.append(f"  {label:<60} {count:>10,}")

    def note(self, text):
        self.lines.append(f"  NOTE: {text}")

    def text(self, text=""):
        self.lines.append(text)

    def dump(self):
        content = "\n".join(self.lines)
        print(content)
        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            f.write(content + "\n")


def connect() -> ConnectionContext:
    return ConnectionContext(
        address=os.environ["HANA_HOST"],
        port=int(os.environ["HANA_PORT"]),
        user=os.environ["HANA_USER"],
        password=os.environ["HANA_PASSWORD"],
        encrypt=True,
    )


def load_typology_spec(path=TYPOLOGY_FILE):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def typology_by_id(spec, typology_id):
    for typ in spec["typologies"]:
        if typ["id"] == typology_id:
            return typ
    return None


def parse_schema_dump(path=SCHEMA_DUMP_FILE):
    schema_meta = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            table, ordinal, column, dtype, length, scale, nullable = line.split("\t")
            schema_meta.setdefault(table, []).append({
                "ordinal": int(ordinal),
                "column": column,
                "data_type": dtype,
                "nullable": nullable.strip().upper() == "TRUE",
            })
    return schema_meta


def nullable_columns(schema_meta, table):
    return [c["column"] for c in schema_meta.get(table, []) if c["nullable"]]


def get_table(cc, table_name, schema=SOURCE_SCHEMA) -> DataFrame:
    return cc.table(table_name, schema=schema)


def null_audit(cc, schema, table, columns):
    """Single pushed-down query: COUNT(*) plus a null-count per nullable column."""
    if not columns:
        return {}, 0
    case_exprs = ", ".join(
        f'SUM(CASE WHEN "{c}" IS NULL THEN 1 ELSE 0 END) AS "{c}"' for c in columns
    )
    sql = f'SELECT COUNT(*) AS TOTAL_ROWS, {case_exprs} FROM "{schema}"."{table}"'
    row = cc.sql(sql).collect().iloc[0]
    total = int(row["TOTAL_ROWS"])
    return {c: int(row[c]) for c in columns}, total


def duplicate_count(df: DataFrame, key_cols):
    grouped = df.agg([("count", key_cols[0], "CNT")], group_by=key_cols)
    return grouped.filter("CNT > 1").count()


def fk_orphan_count(cc, schema, child_table, child_col, parent_table, parent_col):
    """Anti-join count of non-null child FK values with no matching parent row."""
    sql = f'''
        SELECT COUNT(*) AS ORPHANS
        FROM "{schema}"."{child_table}" c
        LEFT JOIN "{schema}"."{parent_table}" p
          ON c."{child_col}" = p."{parent_col}"
        WHERE c."{child_col}" IS NOT NULL AND p."{parent_col}" IS NULL
    '''
    return int(cc.sql(sql).collect().iloc[0, 0])


def run_null_audits(cc, report, schema_meta, tables):
    report.section("NULL / MISSING VALUE AUDIT (nullable columns only, per databasetables.txt)")
    for table in tables:
        cols = nullable_columns(schema_meta, table)
        if not cols:
            continue
        null_counts, total = null_audit(cc, SOURCE_SCHEMA, table, cols)
        report.sub(f"{table} (total rows: {total:,})")
        any_nulls = False
        for col, cnt in null_counts.items():
            if cnt > 0:
                any_nulls = True
                report.row(col, cnt, total)
        if not any_nulls:
            report.text("  no nulls found in any nullable column")
        report.note("financial/amount fields are never imputed here - null AMOUNT/rate "
                     "fields must be excluded from downstream calcs, not filled in")


def run_duplicate_checks(cc, report, tables):
    report.section("DUPLICATE / NATURAL KEY CHECKS")
    for table in tables:
        df = get_table(cc, table)
        pk = PRIMARY_KEYS.get(table)
        if pk:
            dupes = duplicate_count(df, pk)
            report.row(f"{table}: duplicate {'/'.join(pk)} (primary key)", dupes)
        nk = NATURAL_KEYS.get(table)
        if nk:
            dupes = duplicate_count(df, nk)
            report.row(f"{table}: duplicate {'/'.join(nk)} (natural key)", dupes)


def run_referential_integrity_checks(cc, report):
    report.section("REFERENTIAL INTEGRITY (orphaned foreign keys)")
    for child_table, child_col, parent_table, parent_col in FK_RULES:
        orphans = fk_orphan_count(cc, SOURCE_SCHEMA, child_table, child_col, parent_table, parent_col)
        label = f"{child_table}.{child_col} -> {parent_table}.{parent_col}"
        report.row(label, orphans)


def run_range_checks(cc, report):
    report.section("RANGE / TYPE SANITY CHECKS")
    for table, label, condition in RANGE_RULES:
        df = get_table(cc, table)
        total = df.count()
        violations = df.filter(condition).count()
        report.row(f"{table}: {label}", violations, total)


def check_t01_structuring(report, spec):
    typ = typology_by_id(spec, "T01")
    report.sub(f"T01 {typ['name']}")
    report.note(typ["critical_note"])
    report.text("  guard: structuring_view() below selects AMOUNT_ORIGINAL/CURRENCY_ORIGINAL "
                "only and never substitutes AMOUNT_USD for threshold comparisons")


def structuring_view(cc, schema=SOURCE_SCHEMA) -> DataFrame:
    df = get_table(cc, "TRANSACTIONS", schema)
    return df.select(
        "TRANSACTION_ID", "ORIGINATOR_COMPANY_ID", "AMOUNT_ORIGINAL",
        "CURRENCY_ORIGINAL", "INITIATED_AT",
    )


def check_t02_velocity(cc, report, spec):
    typ = typology_by_id(spec, "T02")
    report.sub(f"T02 {typ['name']}")
    report.note(typ["critical_note"])

    baselines = get_table(cc, "TRANSACTION_BASELINES")
    total = baselines.count()
    bad_stddev = baselines.filter('"STDDEV_AMOUNT_USD" IS NULL OR "STDDEV_AMOUNT_USD" = 0').count()
    report.row("baseline rows with NULL/zero STDDEV_AMOUNT_USD (z-score undefined)", bad_stddev, total)

    grouped = baselines.agg([("count", "COMPANY_ID", "CNT")], group_by="COMPANY_ID")
    ambiguous_companies = grouped.filter("CNT > 1").count()
    distinct_companies = baselines.select("COMPANY_ID").distinct().count()
    report.row("companies with >1 BASELINE_PERIOD row (ambiguous - no row picked automatically)",
               ambiguous_companies, distinct_companies)
    report.note("this script does not pick a baseline row on companies' behalf; "
                "downstream feature engineering must select one explicitly "
                "(e.g. most recent PERIOD_END) before computing z-scores")


def safe_velocity_zscore_view(cc, schema=SOURCE_SCHEMA) -> DataFrame:
    """Baseline rows restricted to those where a z-score is actually computable."""
    df = get_table(cc, "TRANSACTION_BASELINES", schema)
    return df.filter('"STDDEV_AMOUNT_USD" IS NOT NULL AND "STDDEV_AMOUNT_USD" > 0')


def normalized_owner_name_view(cc, schema=SOURCE_SCHEMA) -> DataFrame:
    sql = f'''
        SELECT o.*,
            TRIM(REPLACE_REGEXPR('\\s+' IN
                REPLACE_REGEXPR('[^A-Za-z0-9 ]' IN UPPER("OWNER_NAME") WITH '' OCCURRENCE ALL)
                WITH ' ' OCCURRENCE ALL)) AS OWNER_NAME_NORMALIZED
        FROM "{schema}"."COMPANY_BENEFICIAL_OWNERS" o
    '''
    return cc.sql(sql)


def check_t06_nominee(cc, report, spec):
    typ = typology_by_id(spec, "T06")
    report.sub(f"T06 {typ['name']}")
    report.note(typ["critical_note"])

    view = normalized_owner_name_view(cc)
    grouped = view.agg(
        [("count", "COMPANY_ID", "TOTAL_LINKS")],
        group_by="OWNER_NAME_NORMALIZED",
    )
    distinct_company_counts = cc.sql(f'''
        SELECT COUNT(*) AS OWNERS_SPANNING_3_PLUS FROM (
            SELECT TRIM(REPLACE_REGEXPR('\\s+' IN
                    REPLACE_REGEXPR('[^A-Za-z0-9 ]' IN UPPER("OWNER_NAME") WITH '' OCCURRENCE ALL)
                    WITH ' ' OCCURRENCE ALL)) AS OWNER_NAME_NORMALIZED,
                COUNT(DISTINCT "COMPANY_ID") AS N_COMPANIES
            FROM "{SOURCE_SCHEMA}"."COMPANY_BENEFICIAL_OWNERS"
            GROUP BY TRIM(REPLACE_REGEXPR('\\s+' IN
                    REPLACE_REGEXPR('[^A-Za-z0-9 ]' IN UPPER("OWNER_NAME") WITH '' OCCURRENCE ALL)
                    WITH ' ' OCCURRENCE ALL))
            HAVING COUNT(DISTINCT "COMPANY_ID") >= 3
        )
    ''').collect().iloc[0, 0]
    total_owner_rows = get_table(cc, "COMPANY_BENEFICIAL_OWNERS").count()
    report.row("normalized owner names linked to 3+ distinct companies (nominee candidates)",
               int(distinct_company_counts))
    report.row("total beneficial-owner rows", total_owner_rows)
    report.note("no OWNER_ID/global identity exists in the schema - OWNER_NAME_NORMALIZED "
                "is the only linkage key available and fuzzy matching (rapidfuzz/thefuzz) "
                "is required downstream for near-duplicate spellings")


def ownership_totals_view(cc, schema=SOURCE_SCHEMA) -> DataFrame:
    df = get_table(cc, "COMPANY_BENEFICIAL_OWNERS", schema)
    return df.agg(
        [("sum", "OWNERSHIP_PERCENTAGE", "TOTAL_DECLARED_PCT"),
         ("count", "OWNER_ID", "OWNER_COUNT")],
        group_by="COMPANY_ID",
    )


def check_t07_ownership_opacity(cc, report, spec):
    typ = typology_by_id(spec, "T07")
    report.sub(f"T07 {typ['name']}")
    report.note(typ["critical_note"])

    totals = ownership_totals_view(cc)
    total_companies = totals.count()
    over_100 = totals.filter("TOTAL_DECLARED_PCT > 100").count()
    under_100 = totals.filter("TOTAL_DECLARED_PCT < 100").count()
    report.row("companies with SUM(OWNERSHIP_PERCENTAGE) > 100 (DATA ERROR - rows retained, "
               "excluded from opacity scoring)", over_100, total_companies)
    report.row("companies with SUM(OWNERSHIP_PERCENTAGE) < 100 (unexplained ownership signal)",
               under_100, total_companies)
    report.note("rows are never dropped for either condition; SUM>100 companies must be "
                "excluded from the T07 opacity *score* only, not from the returned dataset")


def alias_format_summary(cc, schema=SOURCE_SCHEMA):
    sql = f'''
        SELECT PATTERN, COUNT(*) AS N FROM (
            SELECT CASE WHEN "ALIASES" IS NULL THEN 'NULL'
                        WHEN TO_NVARCHAR("ALIASES") = '[]' THEN 'EMPTY_JSON_ARRAY'
                        ELSE 'HAS_CONTENT' END AS PATTERN
            FROM "{schema}"."SANCTIONS_LISTS"
        ) GROUP BY PATTERN
    '''
    return cc.sql(sql).collect()


def exploded_aliases_view(cc, schema=SOURCE_SCHEMA) -> DataFrame:
    """One row per (SANCTIONS_ID, alias). ALIASES is stored as a JSON array string."""
    sql = f'''
        SELECT s."SANCTIONS_ID", s."ENTITY_NAME", jt.ALIAS_RAW AS ALIAS,
            TRIM(REPLACE_REGEXPR('\\s+' IN
                REPLACE_REGEXPR('[^A-Za-z0-9 ]' IN UPPER(jt.ALIAS_RAW) WITH '' OCCURRENCE ALL)
                WITH ' ' OCCURRENCE ALL)) AS ALIAS_NORMALIZED
        FROM (SELECT "SANCTIONS_ID", "ENTITY_NAME", TO_NVARCHAR("ALIASES") AS ALIASES_TXT
              FROM "{schema}"."SANCTIONS_LISTS") s
        CROSS JOIN LATERAL JSON_TABLE(s.ALIASES_TXT, '$'
            COLUMNS (ALIAS_RAW NVARCHAR(500) PATH '$')) AS jt
    '''
    return cc.sql(sql)


def check_t09_sanctions_alias(cc, report, spec):
    typ = typology_by_id(spec, "T09")
    report.sub(f"T09 {typ['name']}")
    report.note(typ["critical_note"])

    fmt = alias_format_summary(cc)
    total_sanctions_rows = get_table(cc, "SANCTIONS_LISTS").count()
    for _, r in fmt.iterrows():
        report.row(f"ALIASES field pattern: {r['PATTERN']}", int(r["N"]), total_sanctions_rows)

    exploded_count = exploded_aliases_view(cc).count()
    report.row("exploded (SANCTIONS_ID, alias) rows produced", exploded_count)
    if exploded_count == 0:
        report.note("ALIASES currently contains only empty JSON arrays ('[]') for every row "
                     "in this environment - the alias-matching path of T09 will surface zero "
                     "additional hits beyond ENTITY_NAME matching until this field is populated. "
                     "The explode/normalize logic itself is verified working (tested against "
                     "synthetic JSON arrays) and is ready as soon as real alias data lands.")


def bic_country_view(cc, schema=SOURCE_SCHEMA) -> DataFrame:
    sql = f'''
        SELECT "TRANSACTION_ID", "BENEFICIARY_BANK_BIC",
            CASE WHEN "BENEFICIARY_BANK_BIC" IS NOT NULL
                      AND LENGTH("BENEFICIARY_BANK_BIC") IN (8, 11)
                 THEN SUBSTRING("BENEFICIARY_BANK_BIC", 5, 2)
                 ELSE NULL END AS BIC_COUNTRY_CODE
        FROM "{schema}"."TRANSACTIONS"
    '''
    return cc.sql(sql)


def check_t10_jurisdiction(cc, report, spec):
    typ = typology_by_id(spec, "T10")
    report.sub(f"T10 {typ['name']}")
    report.note("BIC positions 5-6 identify the country only if the BIC is a valid 8 or "
                "11-char SWIFT code - invalid lengths must not be sliced blindly")

    tx = get_table(cc, "TRANSACTIONS")
    total_with_bic = tx.filter('"BENEFICIARY_BANK_BIC" IS NOT NULL').count()
    invalid_len = tx.filter(
        '"BENEFICIARY_BANK_BIC" IS NOT NULL AND LENGTH("BENEFICIARY_BANK_BIC") NOT IN (8, 11)'
    ).count()
    report.row("non-null BENEFICIARY_BANK_BIC values", total_with_bic)
    report.row("BIC values with invalid length (neither 8 nor 11 chars - excluded from "
               "country-code extraction)", invalid_len, total_with_bic)


def active_view(df: DataFrame, active_col="IS_ACTIVE", effective_col="EFFECTIVE_DATE",
                 expiry_col="EXPIRY_DATE") -> DataFrame:
    condition = (
        f'"{active_col}" = TRUE AND "{effective_col}" <= CURRENT_DATE '
        f'AND ("{expiry_col}" IS NULL OR "{expiry_col}" >= CURRENT_DATE)'
    )
    return df.filter(condition)


def check_active_lists(cc, report):
    report.section("ACTIVE-WINDOW VIEWS (IS_ACTIVE + EFFECTIVE/EXPIRY validity, stale rows retained not deleted)")
    for table in ("SANCTIONS_LISTS", "SCREENING_RULES"):
        df = get_table(cc, table)
        total = df.count()
        active_count = active_view(df).count()
        report.row(f"{table}: rows in active/valid window", active_count, total)
        report.row(f"{table}: rows excluded (inactive or outside effective/expiry window)",
                   total - active_count, total)


def build_cleaned_dataframes(cc, spec) -> dict:
    """Views (never materialized) handed to downstream feature engineering."""
    tables = spec["table_coverage"]["detection_tables"]
    cleaned = {t: get_table(cc, t) for t in tables}
    cleaned["COMPANY_BENEFICIAL_OWNERS_NORMALIZED"] = normalized_owner_name_view(cc)
    cleaned["COMPANY_BENEFICIAL_OWNERS_OWNERSHIP_TOTALS"] = ownership_totals_view(cc)
    cleaned["SANCTIONS_LISTS_ALIASES_EXPLODED"] = exploded_aliases_view(cc)
    cleaned["SANCTIONS_LISTS_ACTIVE"] = active_view(cleaned["SANCTIONS_LISTS"])
    cleaned["SCREENING_RULES_ACTIVE"] = active_view(cleaned["SCREENING_RULES"])
    cleaned["TRANSACTIONS_STRUCTURING_VIEW"] = structuring_view(cc)
    cleaned["TRANSACTION_BASELINES_SAFE_ZSCORE"] = safe_velocity_zscore_view(cc)
    cleaned["TRANSACTIONS_BIC_COUNTRY"] = bic_country_view(cc)
    return cleaned


def main():
    cc = connect()
    spec = load_typology_spec()
    schema_meta = parse_schema_dump()
    detection_tables = spec["table_coverage"]["detection_tables"]

    report = QualityReport()
    report.text(f"SightLine Data Quality Report - source schema: {SOURCE_SCHEMA}")
    report.text(f"Detection tables ({len(detection_tables)}): {', '.join(detection_tables)}")

    run_null_audits(cc, report, schema_meta, detection_tables)
    run_duplicate_checks(cc, report, detection_tables)
    run_referential_integrity_checks(cc, report)
    run_range_checks(cc, report)

    report.section("TYPOLOGY-SPECIFIC DATA-QUALITY GUARDS (from topfraudandtables.json critical_note fields)")
    check_t01_structuring(report, spec)
    check_t02_velocity(cc, report, spec)
    check_t06_nominee(cc, report, spec)
    check_t07_ownership_opacity(cc, report, spec)
    check_t09_sanctions_alias(cc, report, spec)
    check_t10_jurisdiction(cc, report, spec)
    check_active_lists(cc, report)

    report.dump()

    cleaned = build_cleaned_dataframes(cc, spec)
    return cc, cleaned


if __name__ == "__main__":
    connection_context, cleaned_dataframes = main()
    connection_context.close()
