# AI Procurement Intelligence

Spend analytics, anomaly detection and supplier risk on French public procurement data (DECP), with machine learning and LLM explanations. Work in progress.

## Data

The project uses open data only:

- **Données essentielles de la commande publique consolidées**, published on data.gouv.fr by Colin Maudry (decp.info) under the Licence Ouverte 2.0;
- **Base Sirene** (INSEE), stock of legal units, Licence Ouverte 2.0;
- **CPV 2008 code list**, from the eForms SDK of the Publications Office of the European Union, CC BY 4.0.

The snapshot used for all results is frozen in `data/snapshot/` by the **Build the data snapshot** workflow; `data/snapshot/manifest.json` records the sources, download dates and checksums.
