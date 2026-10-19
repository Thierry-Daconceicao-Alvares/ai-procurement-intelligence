# Power BI report

The folder `powerbi/` holds a Power BI Project (PBIP): a semantic model in TMDL
and a four-page report in the PBIR format, both stored as text files. It reads
the same Parquet marts as the Streamlit application (`data/marts/`), so the two
show the same figures.

The project was generated as text files by `scripts/powerbi/build_pbip.py`. It
is validated without Power BI Desktop: the report and project files against
Microsoft's published JSON schemas, the references between visuals, measures,
columns and relationships by `scripts/powerbi/validate.py`, and the TMDL by the
Tabular Object Model in CI (`.github/workflows/powerbi.yml`). These checks do not
render the report nor evaluate the DAX and Power Query code: only Power BI
Desktop does. Open it once in Desktop before relying on it.

## Open the report

1. Get the repository with its marts: clone it (the Parquet files of
   `data/marts/` are committed) or download it as a ZIP and extract it.
2. Open `powerbi/ProcurementIntelligence.pbip` in a recent version of Power BI
   Desktop for Windows. If Desktop asks for them, turn on the preview features
   *Power BI Project (.pbip) save option*, *Store semantic model using TMDL
   format* and *Store reports using enhanced metadata format (PBIR)* in
   File > Options and settings > Options > Preview features, then restart it.
3. Home > Transform data > Edit parameters: set **DataFolder** to the full path
   of `data/marts` in your copy, for example
   `C:\Users\you\ai-procurement-intelligence\data\marts`.
4. Apply the changes, then Home > Refresh.

The refresh reads about 130 MB of Parquet: about 1.5 million contracts, 1.6
million contract holders and 0.3 million alerts. Its duration depends on the
machine and has not been measured. Desktop stores the loaded data in
`.pbi/cache.abf` and its local settings in `.pbi/localSettings.json`; both are
excluded from Git by `powerbi/.gitignore`.

## Semantic model

| Table | Grain | Source |
|---|---|---|
| Contracts | One contract, after merging the records of one award | `contracts/` |
| ContractHolders | One holder of a contract, with its share of the amount | `contract_holders/` |
| Buyers | One public buyer (SIRET) | `buyers.parquet` |
| Suppliers | One supplier (SIREN), with its segment when it has at least three contracts | `suppliers.parquet` and `supplier_profiles.parquet` |
| CPV | One CPV 2008 code with its division, group and class | `cpv.parquet` |
| Alerts | One alert to review, with the names of its buyer and supplier (without the JSON evidence) | `alerts/`, `buyers.parquet`, `suppliers.parquet` |
| CategoryMarkets | One CPV group: concentration, competition, Kraljic quadrant | `category_markets.parquet` |
| Dates | Calendar of the analysis window, marked as the date table | `dates.parquet` |
| Measures | The measures of the report, in display folders | (calculated table) |

All tables are imported. Each Power Query query starts from the `DataFolder`
parameter; the queries that combine several files (Suppliers, Alerts) read
them from one folder data source.

Relationships (many to one, single direction):

- Contracts[buyer_siret] → Buyers[buyer_siret]
- Contracts[cpv_code] → CPV[cpv_code]
- Contracts[notification_date] → Dates[date]
- ContractHolders[uid] → Contracts[uid]
- ContractHolders[holder_key] → Suppliers[holder_key]
- Alerts[uid] → Contracts[uid]
- CPV[cpv_group] → CategoryMarkets[cpv_group]

Dates, Buyers and CPV filter Contracts, which filters ContractHolders and
Alerts. A supplier filter (Suppliers) reaches ContractHolders only: on the
Suppliers page, the cards and tables use supplier measures.

### Measures

| Folder | Measure | Definition |
|---|---|---|
| Spend | Awarded Value | `SUM ( Contracts[awarded_amount] )`: valid amounts only |
| Spend | Contract Value, Framework Value | Awarded Value of the contracts that are not, or are, framework agreements |
| Spend | Contracts, Buyers | `COUNTROWS ( Contracts )`, `DISTINCTCOUNT ( Contracts[buyer_siret] )` |
| Spend | Awarded Value YoY % | Change over the same period one year earlier (`DATEADD`) |
| Competition | Single-offer Rate | Competitive procedures with one offer, among those that report their number of offers |
| Competition | Offer Reporting Share | Competitive procedures that report their number of offers |
| Competition | Single-offer Rate (reported years) | Single-offer Rate where Offer Reporting Share is at least 50% |
| Competition | Non-competitive Share | Contracts awarded without publication or competition |
| Suppliers | Supplier Value, Suppliers, Supplier Contracts | Allocated amounts, distinct holders, distinct contracts of ContractHolders |
| Suppliers | SME Share of Value | Supplier Value held by SMEs |
| Suppliers | Top 10 Supplier Share | `TOPN ( 10, VALUES ( ContractHolders[holder_key] ), [Supplier Value] )` over Supplier Value |
| Suppliers | Supplier HHI | Sum of squared supplier shares × 10,000 |
| Suppliers | Profiled Suppliers | Suppliers that have a segment |
| Alerts | Alerts, High-severity Alerts, Amount at Stake | Count, count of high severity, sum of the amount each alert is about (not a loss) |
| Data quality | Implausible Amounts | Contracts whose reported amount is excluded from totals |
| Markets | Market Value, Market Value Percentile, Market Supply Risk | Values of CategoryMarkets for the Kraljic matrix |

The full DAX is in
`powerbi/ProcurementIntelligence.SemanticModel/definition/tables/Measures.tmdl`.

## Report pages

1. **Spend overview**: slicers for year, buyer region, buyer category and
   contract type; cards for awarded value, contracts, buyers, suppliers,
   single-offer rate and SME share; awarded value by year (contracts and
   framework maximums, stacked); awarded value by CPV division; single-offer
   rate by year, for the years in which at least half of the competitive
   procedures report their offers.
2. **Suppliers**: cards for suppliers, share of the 10 largest, HHI and SME
   share; the largest suppliers; suppliers per segment.
3. **Categories and markets**: the Kraljic matrix of CPV groups (value at stake
   against supply risk, coloured by quadrant) and the table of markets.
4. **Alerts**: slicers for category, severity, alert and year; cards for
   alerts, high-severity alerts and amount at stake; the review queue ordered
   by priority. Buyer-level alerts have no contract, so a year filter hides
   them.

The year, region and buyer category slicers are synchronised across pages. The
report uses the palette of the application through a custom theme
(`StaticResources/RegisteredResources/ProcurementIntelligence.json`).

## Screenshots

After the first refresh, save one image per page, at 1280 × 720, in
`docs/images/`: `powerbi-overview.png`, `powerbi-suppliers.png`,
`powerbi-categories.png` and `powerbi-alerts.png`.

## Change the project

Once the project has been opened and saved in Desktop, edit it in Desktop: the
TMDL and PBIR files it writes are the reference. `python
scripts/powerbi/build_pbip.py` rewrites the whole project from its definitions
and would overwrite changes made in Desktop. After any change, run:

```bash
python scripts/powerbi/validate.py                     # references
python scripts/powerbi/validate.py --schemas <clone>   # plus JSON schemas
pytest tests/test_powerbi.py                           # references and mart schemas
```

where `<clone>` is a clone of
[microsoft/json-schemas](https://github.com/microsoft/json-schemas).
