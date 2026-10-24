# Fixture provenance

The public source profile contains only these original synthetic inputs:

| Input | Purpose | Origin |
| --- | --- | --- |
| `india/synthetic.csv` | Fictional DEMO intraday entry, exits and costs | Original synthetic values |
| `india/workflow/security-master.csv` | Dated tick validation | Fictional DEMO metadata; not a real instrument record |
| `india/workflow/financials.json` | Comparable quarters and future-publication exclusion | Invented issuer financials |
| `india/workflow/disclosures.csv` | Public-disclosure parser and timing | Invented transactions and people |
| `india/workflow/yahoo-DEMO.NS.xml` | RSS parsing and cutoff checks | Original headlines and reserved `.invalid` URLs |

`tests/_provider_fixtures.py` generates original NSE-shaped and Yahoo-shaped
payloads from arithmetic sequences. They test parser contracts, IST encoding,
missing candles, auction exclusion and causal replays without reproducing
captured prices. Their `fixture` fields and DEMO symbol are deliberate labels.
Other active tests construct synthetic values in memory and mock network calls.

The development checkout may additionally contain historical `trader-*`
fixtures and `india/tcs-*` captures. Those are not part of the public source
profile; filenames, public accessibility and the software license do not
establish their redistribution permission. The associated historical US tests
are also excluded. Do not relabel captures as synthetic or add them to the
release manifest without establishing provenance and applicable permissions.

Add new synthetic inputs explicitly to `release-manifest.json`, describe their
construction here and cover the behavior they exercise. The exporter rejects
fixtures selected indirectly by source globs.
