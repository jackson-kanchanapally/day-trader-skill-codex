"""Append-only SQLite candle vintages and dated research evidence."""
import datetime as dt
import json
import math
from pathlib import Path
import sqlite3
from typing import Optional

from india.audit import canonical, digest, encode_bars
from india.data import Bar, IST, symbol_name, timestamp, validate
from .regimes import record as regime_record, select as select_regime


def instant(value) -> dt.datetime:
    return timestamp(value.isoformat() if isinstance(value, dt.datetime) else value)


def source_record(source: dict) -> dict:
    if (not isinstance(source, dict) or set(source) != {'provider', 'reference', 'license_note'}
            or any(not isinstance(v, str) or not v.strip() for v in source.values())):
        raise ValueError('Source requires provider, reference and license_note strings; permissions are caller verified')
    return source


class CandleStore:
    """No network access. Conflicting revisions are retained, never overwritten.

    observed_at is a supplied source-vintage timestamp, not an authentication
    claim. recorded_at independently records when this database received it.
    """
    def __init__(self, path, *, readonly: bool = False):
        path = Path(path).resolve()
        if readonly:
            if not path.is_file():
                raise ValueError('Historical store does not exist')
            self.db = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(path, timeout=10)
            existing = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_schema'").fetchone()
            if existing and self.db.execute('SELECT version FROM research_schema').fetchall() != [(1,)]:
                self.db.close()
                raise ValueError('Unsupported research store schema')
            with self.db:
                self.db.execute('CREATE TABLE IF NOT EXISTS research_schema (version INTEGER PRIMARY KEY)')
                self.db.execute('INSERT OR IGNORE INTO research_schema VALUES (1)')
                self.db.execute('CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, recorded_at TEXT NOT NULL, document TEXT NOT NULL)')
                self.db.execute('CREATE TABLE IF NOT EXISTS candles (symbol TEXT NOT NULL, time TEXT NOT NULL, observed_at TEXT NOT NULL, document TEXT NOT NULL, batch_id TEXT NOT NULL, payload_sha256 TEXT NOT NULL, PRIMARY KEY(symbol,time,observed_at))')
                self.db.execute('CREATE TABLE IF NOT EXISTS evidence (kind TEXT NOT NULL, identity TEXT NOT NULL, observed_at TEXT NOT NULL, document TEXT NOT NULL, payload_sha256 TEXT NOT NULL, PRIMARY KEY(kind,identity,observed_at))')
        if self.db.execute('SELECT version FROM research_schema').fetchall() != [(1,)]:
            self.db.close()
            raise ValueError('Unsupported research store schema')

    def close(self):
        self.db.close()

    def ingest(self, symbol: str, bars: list, *, source: dict, observed_at,
               input_sha256: Optional[str] = None) -> dict:
        symbol_name(symbol)
        validate(bars)
        source_record(source)
        observed = instant(observed_at)
        if observed > dt.datetime.now(IST):
            raise ValueError('Source vintage cannot be in the future')
        for bar in bars:
            if bar.time+dt.timedelta(minutes=5) > observed:
                raise ValueError('Source vintage precedes candle completion')
            opening = bar.time.replace(hour=9, minute=15, second=0, microsecond=0)
            if not dt.time(9,15) <= bar.time.time() < dt.time(15,30) or (bar.time-opening).total_seconds() % 300:
                raise ValueError('Historical store accepts grid-aligned 09:15-15:30 five-minute candles only')
        snapshot = encode_bars(bars)
        document = dict(symbol=symbol, interval='5m', currency='INR', adjustment='raw',
                        source=source, observed_at=observed.isoformat(),
                        input_sha256=input_sha256, candles_sha256=digest(snapshot),
                        first_bar=bars[0].time.isoformat(), last_bar=bars[-1].time.isoformat(), rows=len(bars))
        batch_id = digest(document)
        written, unchanged = 0, 0
        with self.db:
            if self.db.execute('SELECT 1 FROM batches WHERE id=?', (batch_id,)).fetchone():
                return dict(batch_id=batch_id, written=0, unchanged=len(bars), idempotent=True)
            # Importing an older vintage after a newer one is allowed. Querying
            # always uses explicit vintage order, not import/insertion order.
            for bar in snapshot:
                when, payload = bar['time'], canonical(bar)
                exact = self.db.execute('SELECT document FROM candles WHERE symbol=? AND time=? AND observed_at=?',
                                        (symbol, when, observed.isoformat())).fetchone()
                if exact:
                    if exact[0] != payload:
                        raise ValueError('Conflicting candle at the same source vintage; use a distinct revision timestamp')
                    unchanged += 1
                    continue
                previous = self.db.execute('SELECT document FROM candles WHERE symbol=? AND time=? AND observed_at<=? ORDER BY observed_at DESC LIMIT 1',
                                           (symbol, when, observed.isoformat())).fetchone()
                if previous and previous[0] == payload:
                    unchanged += 1
                self.db.execute('INSERT INTO candles VALUES (?,?,?,?,?,?)', (symbol, when, observed.isoformat(), payload, batch_id, digest(bar)))
                written += 1
            self.db.execute('INSERT INTO batches VALUES (?,?,?,?)',
                            (batch_id, observed.isoformat(), dt.datetime.now(IST).isoformat(), canonical(document)))
        return dict(batch_id=batch_id, written=written, unchanged=unchanged, idempotent=False)

    def load(self, symbol: str, *, start: dt.date, end: dt.date, vintage) -> tuple:
        symbol_name(symbol)
        if start > end:
            raise ValueError('Historical range start must not follow end')
        cutoff = instant(vintage).isoformat()
        records = self.db.execute('SELECT time,document,batch_id,payload_sha256 FROM candles WHERE symbol=? AND time>=? AND time<? AND observed_at<=? ORDER BY time,observed_at DESC',
                                  (symbol, start.isoformat(), (end+dt.timedelta(days=1)).isoformat(), cutoff)).fetchall()
        selected = {}
        for when, payload, batch_id, checksum in records:
            selected.setdefault(when, (payload, batch_id, checksum))
        bars, batches = [], set()
        for when, (payload, batch_id, checksum) in selected.items():
            row = json.loads(payload)
            if digest(row) != checksum or row['time'] != when:
                raise ValueError('Stored candle integrity check failed')
            bars.append(Bar(timestamp(row['time']), *(row[k] for k in ('open','high','low','close','volume'))))
            batches.add(batch_id)
        if bars:
            validate(bars)
        provenance = []
        for batch_id in sorted(batches):
            row = self.db.execute('SELECT document,recorded_at FROM batches WHERE id=?', (batch_id,)).fetchone()
            if row is None:
                raise ValueError('Stored candle has missing batch provenance')
            document = json.loads(row[0])
            if digest(document) != batch_id or document['symbol'] != symbol:
                raise ValueError('Stored batch provenance integrity check failed')
            provenance.append(dict(document, batch_id=batch_id, recorded_at=row[1]))
        if len({batch['source']['provider'] for batch in provenance}) > 1:
            raise ValueError('Historical selection mixes providers; use separate stores or a single consistent source')
        return bars, dict(vintage=cutoff, batches=provenance, selected_candles_sha256=digest(encode_bars(bars)) if bars else digest([]))

    def watermark(self, symbol: str, *, vintage) -> Optional[str]:
        symbol_name(symbol)
        row = self.db.execute('SELECT MAX(time) FROM candles WHERE symbol=? AND observed_at<=?', (symbol, instant(vintage).isoformat())).fetchone()
        return row[0]

    def evidence(self, kind: str, records: list, *, observed_at) -> int:
        if kind not in ('instrument', 'action', 'regime') or not isinstance(records, list) or not records:
            raise ValueError('Evidence requires nonempty instrument/action/regime records')
        observed = instant(observed_at)
        if observed > dt.datetime.now(IST):
            raise ValueError('Evidence observation is in the future')
        documents = []
        for original in records:
            row = regime_record(original) if kind == 'regime' else dict(original)
            if kind != 'regime':
                symbol_name(row['symbol'])
            if not isinstance(row.get('source'), str) or not row['source'].strip():
                raise ValueError('Evidence requires a source')
            published = instant(row['published_at'])
            if published > observed:
                raise ValueError('Evidence vintage precedes publication')
            row['published_at'] = published.isoformat()
            if kind == 'instrument':
                required = {'symbol','effective_from','effective_to','published_at','source','tick','sector','eligible'}
                if set(row) != required or type(row['eligible']) is not bool or not isinstance(row['sector'], str) or not row['sector'].strip():
                    raise ValueError('Instrument evidence requires dated eligibility, tick and sector')
                start, end = dt.date.fromisoformat(row['effective_from']), dt.date.fromisoformat(row['effective_to'])
                if start > end or type(row['tick']) not in (int,float) or not math.isfinite(row['tick']) or row['tick'] <= 0:
                    raise ValueError('Invalid instrument validity/tick')
                identity = row['symbol']+':'+row['effective_from']+':'+row['published_at']
            elif kind == 'action':
                required = {'id','symbol','kind','effective_date','published_at','source','price_factor'}
                if set(row) != required or row['kind'] not in ('split','bonus'):
                    raise ValueError('Only explicit split/bonus price factors are supported; cash dividends are not inferred')
                dt.date.fromisoformat(row['effective_date'])
                factor = row['price_factor']
                if type(factor) not in (int,float) or not math.isfinite(factor) or factor <= 0 or not isinstance(row['id'],str) or not row['id']:
                    raise ValueError('Corporate action needs an ID and positive price factor')
                identity = row['symbol']+':'+row['id']+':'+row['published_at']
            else:
                identity = ':'.join((row['exchange'],row['model'],row['effective_from'],row['published_at']))
            documents.append((kind, identity, observed.isoformat(), canonical(row), digest(row)))
        count = 0
        with self.db:
            if kind == 'regime':
                # Serialize definition checks with imports from other writers.
                if not self.db.in_transaction:
                    self.db.execute('BEGIN IMMEDIATE')
                definitions, methods = {}, {}
                for row in self._evidence(kind,dt.datetime.now(IST))+[json.loads(v[3]) for v in documents]:
                    key = row['exchange'],row['model']
                    definition = row['benchmark'],row['method']
                    if ((key in definitions and definitions[key] != definition)
                            or (row['model'] in methods and methods[row['model']] != row['method'])):
                        raise ValueError('Regime benchmark/method changed; use a new versioned model identifier')
                    definitions[key] = definition
                    methods[row['model']] = row['method']
            for values in documents:
                prior = self.db.execute('SELECT document FROM evidence WHERE kind=? AND identity=? AND observed_at=?', values[:3]).fetchone()
                if prior and prior[0] != values[3]:
                    raise ValueError('Conflicting evidence at the same vintage')
                if not prior:
                    self.db.execute('INSERT INTO evidence VALUES (?,?,?,?,?)', values)
                    count += 1
        return count

    def _evidence(self, kind: str, vintage) -> list:
        rows = self.db.execute('SELECT identity, document, payload_sha256 FROM evidence WHERE kind=? AND observed_at<=? ORDER BY observed_at DESC',
                               (kind, instant(vintage).isoformat())).fetchall()
        selected = {}
        for identity, payload, checksum in rows:
            row = json.loads(payload)
            if digest(row) != checksum:
                raise ValueError('Stored evidence integrity check failed')
            selected.setdefault(identity, row)
        return list(selected.values())

    def regime_records(self, *, vintage) -> list:
        return self._evidence('regime',vintage)

    def regime(self, day: dt.date, exchange: str, model: str, *, vintage) -> Optional[dict]:
        return select_regime(self.regime_records(vintage=vintage),day,exchange,model)

    def universe(self, day: dt.date, *, vintage) -> dict:
        opening = dt.datetime.combine(day, dt.time(9,15), IST)
        versions = {}
        # Resolve a record revision before testing its validity window: an
        # updated delisting date must not resurrect the superseded open record.
        for row in self._evidence('instrument', vintage):
            key = row['symbol'], row['effective_from']
            if instant(row['published_at']) <= opening:
                if key not in versions or row['published_at'] > versions[key]['published_at']:
                    versions[key] = row
        selected = {}
        for row in versions.values():
            if row['effective_from'] <= day.isoformat() <= row['effective_to']:
                previous = selected.get(row['symbol'])
                if previous and row['published_at'] == previous['published_at'] and row != previous:
                    raise ValueError('Ambiguous overlapping instrument evidence at the same publication time')
                if previous is None or row['published_at'] > previous['published_at']:
                    selected[row['symbol']] = row
        return {symbol: row for symbol,row in sorted(selected.items()) if row['eligible']}

    def actions(self, symbol: str, *, vintage, through: dt.date, known_at=None) -> list:
        selected = {}
        for row in self._evidence('action', vintage):
            if row['symbol'] == symbol and instant(row['published_at']) <= min(instant(vintage),instant(known_at) if known_at is not None else instant(vintage)):
                if row['id'] not in selected or row['published_at'] > selected[row['id']]['published_at']:
                    selected[row['id']] = row
        return sorted((row for row in selected.values() if row['effective_date'] <= through.isoformat()),
                      key=lambda row:(row['effective_date'],row['id']))

    def adjusted(self, symbol: str, *, start: dt.date, end: dt.date, vintage) -> tuple:
        """Analytical backward split/bonus view; execution always consumes raw."""
        bars, provenance = self.load(symbol, start=start, end=end, vintage=vintage)
        actions = self.actions(symbol, vintage=vintage, through=end)
        adjusted = []
        for bar in bars:
            factor = 1.
            for action in actions:
                if bar.time.date().isoformat() < action['effective_date']:
                    factor *= action['price_factor']
            adjusted.append(Bar(bar.time, bar.open*factor, bar.high*factor, bar.low*factor, bar.close*factor, bar.volume/factor))
        return adjusted, dict(provenance, adjustment='backward split/bonus; analytical only', actions=actions)
