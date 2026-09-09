"""Reviewed fine-tuning examples and immutable local run snapshots."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import re
import uuid

from . import filesystem as fs
from .store import now
from .strand import safe_directory, safe_read, safe_write


MAX_IMPORT_BYTES = 16 * 1024 * 1024
MAX_EXAMPLES = 10_000
MAX_TEXT = 100_000
SPLITS = {'train', 'eval'}
STATUSES = {'queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted'}
TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted'}


@dataclass
class TrainingConfig:
    python_executable: str | Path
    base_model: str | Path
    base_gguf: str | Path = ''
    llama_cpp_dir: str | Path = ''
    epochs: int = 1
    learning_rate: float = 0.0002
    rank: int = 8
    max_length: int = 512
    batch_size: int = 1
    seed: int = 42
    device: str = 'cpu'

    def validate(self):
        python = Path(self.python_executable).expanduser()
        model = Path(self.base_model).expanduser()
        if not python.is_file():
            raise ValueError('Training Python executable must be an existing local file')
        if not model.is_dir():
            raise ValueError('Base model must be an existing local directory')
        if not any(model.glob('*.safetensors')) and not any(model.glob('*.safetensors.index.json')):
            raise ValueError('Base model directory must contain local safetensors weights')
        if self.base_gguf and not Path(self.base_gguf).expanduser().is_file():
            raise ValueError('Base GGUF must be an existing local file')
        if self.llama_cpp_dir and not Path(self.llama_cpp_dir).expanduser().is_dir():
            raise ValueError('llama.cpp checkout must be an existing local directory')
        bounds = (
            ('epochs', self.epochs, 1, 100), ('rank', self.rank, 1, 256),
            ('max_length', self.max_length, 8, 8192), ('batch_size', self.batch_size, 1, 64),
            ('seed', self.seed, 0, 2**31 - 1),
        )
        for name, value, minimum, maximum in bounds:
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f'{name} must be an integer from {minimum} to {maximum}')
        if (isinstance(self.learning_rate, bool) or not isinstance(self.learning_rate, (int, float))
                or not math.isfinite(self.learning_rate) or not 0 < self.learning_rate <= .1):
            raise ValueError('learning_rate must be a finite number greater than 0 and at most 0.1')
        if not isinstance(self.device, str) or self.device not in ('cpu', 'cuda'):
            raise ValueError("device must be 'cpu' or 'cuda'")

    def to_dict(self):
        self.validate()
        values = asdict(self)
        # Resolving a venv interpreter symlink selects the system environment.
        values['python_executable'] = str(Path(self.python_executable).expanduser().absolute())
        for key in ('base_model', 'base_gguf', 'llama_cpp_dir'):
            values[key] = str(Path(values[key]).expanduser().resolve()) if values[key] else ''
        return values


class TrainingRepository:
    def __init__(self, store):
        self.store = store
        with store.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS training_examples (
                    id TEXT PRIMARY KEY, prompt TEXT NOT NULL, response TEXT NOT NULL,
                    split TEXT NOT NULL CHECK(split IN ('train','eval')),
                    approved INTEGER NOT NULL CHECK(approved IN (0,1)), source TEXT NOT NULL,
                    created TEXT NOT NULL, updated TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS training_examples_order
                    ON training_examples(split, created, id);
                CREATE TABLE IF NOT EXISTS training_runs (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL,
                    config TEXT NOT NULL, examples TEXT NOT NULL,
                    report TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
                    created TEXT NOT NULL, updated TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS training_runs_order ON training_runs(created, id);
            ''')

    @staticmethod
    def _example(row):
        value = dict(row)
        value['approved'] = bool(value['approved'])
        return value

    def examples(self, split=None):
        if split is not None and split not in SPLITS:
            raise ValueError("split must be 'train' or 'eval'")
        sql = 'SELECT * FROM training_examples'
        args = ()
        if split is not None:
            sql += ' WHERE split=?'
            args = (split,)
        sql += ' ORDER BY created,rowid'
        with self.store.connection() as db:
            return [self._example(row) for row in db.execute(sql, args)]

    @staticmethod
    def _fields(prompt, response, split, source):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError('Prompt is required')
        if not isinstance(response, str) or not response.strip():
            raise ValueError('Response is required')
        prompt, response = prompt.strip(), response.strip()
        if len(prompt) > MAX_TEXT or len(response) > MAX_TEXT:
            raise ValueError(f'Prompt and response are limited to {MAX_TEXT} characters')
        if split not in SPLITS:
            raise ValueError("split must be 'train' or 'eval'")
        if not isinstance(source, str) or len(source) > 1000:
            raise ValueError('source must be text of at most 1000 characters')
        return prompt, response, source.strip()

    def save_example(self, prompt, response, split='train', approved=False, source='', example_id=None):
        prompt, response, source = self._fields(prompt, response, split, source)
        if type(approved) is not bool:
            raise ValueError('approved must be true or false')
        timestamp = now()
        with self.store.connection() as db:
            if example_id is None:
                ident = uuid.uuid4().hex
                db.execute('INSERT INTO training_examples VALUES (?,?,?,?,?,?,?,?)',
                           (ident, prompt, response, split, int(approved), source, timestamp, timestamp))
            else:
                current = db.execute('SELECT * FROM training_examples WHERE id=?', (example_id,)).fetchone()
                if current is None:
                    raise ValueError('Training example does not exist')
                changed = (prompt, response, split) != (current['prompt'], current['response'], current['split'])
                final_approval = False if changed else approved
                db.execute('''UPDATE training_examples SET prompt=?,response=?,split=?,approved=?,source=?,updated=?
                              WHERE id=?''',
                           (prompt, response, split, int(final_approval), source, timestamp, example_id))
                ident = example_id
            row = db.execute('SELECT * FROM training_examples WHERE id=?', (ident,)).fetchone()
        return self._example(row)

    def delete_example(self, ident):
        with self.store.connection() as db:
            return db.execute('DELETE FROM training_examples WHERE id=?', (ident,)).rowcount > 0

    @staticmethod
    def _import_row(value, line):
        if not isinstance(value, dict):
            raise ValueError(f'Invalid JSONL schema on line {line}')
        if 'messages' in value:
            unsupported = set(value) - {'messages', 'split', 'approved', 'source'}
            if unsupported:
                raise ValueError(f'JSONL line {line} contains unsupported fields: {sorted(unsupported)}')
            if 'prompt' in value or 'response' in value:
                raise ValueError(f'Invalid JSONL schema on line {line}')
            messages = value['messages']
            if (not isinstance(messages, list) or len(messages) != 2
                    or [message.get('role') for message in messages if isinstance(message, dict)] != ['user', 'assistant']
                    or any(set(message) != {'role', 'content'} for message in messages if isinstance(message, dict))):
                raise ValueError(f'Invalid messages on line {line}: expected exactly one user then one assistant message; system messages are unsupported')
            prompt, response = messages[0].get('content'), messages[1].get('content')
        else:
            unsupported = set(value) - {'prompt', 'response', 'split', 'approved', 'source'}
            if unsupported:
                raise ValueError(f'JSONL line {line} contains unsupported fields: {sorted(unsupported)}')
            if 'prompt' not in value or 'response' not in value:
                raise ValueError(f'Invalid JSONL schema on line {line}')
            prompt, response = value['prompt'], value['response']
        split = value.get('split', 'train')
        source = value.get('source', 'import')
        prompt, response, source = TrainingRepository._fields(prompt, response, split, source)
        return prompt, response, split, source

    def import_jsonl(self, path):
        path = Path(path)
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_IMPORT_BYTES:
            raise ValueError(f'JSONL import must be a regular local file no larger than {MAX_IMPORT_BYTES} bytes')
        rows = []
        try:
            with path.open('r', encoding='utf-8') as stream:
                for line_number, raw in enumerate(stream, 1):
                    if not raw.strip():
                        continue
                    if len(rows) >= MAX_EXAMPLES:
                        raise ValueError(f'JSONL imports are limited to {MAX_EXAMPLES} examples')
                    try:
                        value = json.loads(raw)
                    except (ValueError, RecursionError) as error:
                        raise ValueError(f'Invalid JSON on line {line_number}: {error}') from error
                    rows.append(self._import_row(value, line_number))
        except UnicodeError as error:
            raise ValueError(f'JSONL import must be UTF-8: {error}') from error
        if not rows:
            raise ValueError('JSONL import contains no examples')
        timestamp = now()
        ids = [uuid.uuid4().hex for _ in rows]
        with self.store.connection() as db:
            db.executemany('INSERT INTO training_examples VALUES (?,?,?,?,0,?,?,?)',
                           [(ident, prompt, response, split, source, timestamp, timestamp)
                            for ident, (prompt, response, split, source) in zip(ids, rows)])
            saved = {row['id']: self._example(row) for row in db.execute(
                f"SELECT * FROM training_examples WHERE id IN ({','.join(['?'] * len(ids))})", ids)}
            return [saved[ident] for ident in ids]

    def export_jsonl(self, path):
        content = ''.join(json.dumps({key: row[key] for key in ('prompt', 'response', 'split', 'approved', 'source')},
                                     ensure_ascii=False, allow_nan=False) + '\n'
                          for row in self.examples()).encode('utf-8')
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        expected = None
        if destination.exists():
            import hashlib
            expected = hashlib.sha256(destination.read_bytes()).hexdigest()
        safe_write(destination, content, expected, max_bytes=MAX_IMPORT_BYTES, mode=0o600)

    @staticmethod
    def _normalized_prompt(prompt):
        return ' '.join(prompt.split()).casefold()

    def create_run(self, config):
        if not isinstance(config, TrainingConfig):
            raise TypeError('config must be a TrainingConfig')
        configuration = config.to_dict()
        examples = self.examples()
        approved = {split: [dict(row) for row in examples if row['approved'] and row['split'] == split]
                    for split in SPLITS}
        if not approved['train']:
            raise ValueError('At least one approved training example is required')
        if not approved['eval']:
            raise ValueError('At least one approved evaluation example is required')
        training_prompts = {self._normalized_prompt(row['prompt']) for row in approved['train']}
        if any(self._normalized_prompt(row['prompt']) in training_prompts for row in approved['eval']):
            raise ValueError('Duplicate prompt found across approved training/evaluation data')
        for split, rows in approved.items():
            if len({self._normalized_prompt(row['prompt']) for row in rows}) != len(rows):
                raise ValueError(f'Duplicate prompt in approved {split} examples; review the repeated examples')
        ident = uuid.uuid4().hex
        directory = self.store.directory / 'training' / 'runs' / ident
        parent = directory.parent
        with safe_directory(parent, create=True) as parent_fd:
            fs.mkdir(ident, 0o700, dir_fd=parent_fd)
            fs.fsync(parent_fd)
        with safe_directory(directory):
            pass
        os.chmod(directory, 0o700)
        snapshots = self._snapshot_files(configuration, approved)
        for name, data in snapshots.items():
            safe_write(directory / name, data, None, max_bytes=MAX_IMPORT_BYTES, mode=0o600)
        timestamp = now()
        snapshot_json = json.dumps(approved, ensure_ascii=False, allow_nan=False)
        with self.store.connection() as db:
            db.execute('INSERT INTO training_runs VALUES (?,?,?,?,?,?,?,?)',
                       (ident, 'queued', json.dumps(configuration), snapshot_json, '{}', '', timestamp, timestamp))
        return self.run(ident)

    @staticmethod
    def _snapshot_files(configuration, examples):
        snapshots = {'config.json': json.dumps(configuration, ensure_ascii=False, indent=2).encode('utf-8')}
        for split in ('train', 'eval'):
            snapshots[f'{split}.jsonl'] = ''.join(
                json.dumps({'prompt': row['prompt'], 'response': row['response']}, ensure_ascii=False) + '\n'
                for row in examples[split]).encode('utf-8')
        return snapshots

    def verify_run_snapshot(self, ident):
        run = self.run(ident)
        if run is None:
            raise ValueError('Training run does not exist')
        directory = self.run_directory(ident)
        for name, expected in self._snapshot_files(run['config'], run['examples']).items():
            if safe_read(directory / name, MAX_IMPORT_BYTES) != expected:
                raise ValueError(f'Training snapshot changed: {name}. Create a new reviewed run.')
        return run

    @staticmethod
    def _run(row):
        if row is None:
            return None
        value = dict(row)
        for name in ('config', 'examples', 'report'):
            value[name] = json.loads(value[name])
        return value

    def runs(self):
        with self.store.connection() as db:
            return [self._run(row) for row in db.execute('SELECT * FROM training_runs ORDER BY created DESC,id DESC')]

    def run(self, ident):
        with self.store.connection() as db:
            return self._run(db.execute('SELECT * FROM training_runs WHERE id=?', (ident,)).fetchone())

    def update_run(self, ident, status, report=None, error=''):
        if status not in STATUSES:
            raise ValueError('Invalid training run status')
        if report is not None and not isinstance(report, dict):
            raise ValueError('report must be a JSON object')
        if not isinstance(error, str):
            raise ValueError('error must be text')
        with self.store.connection() as db:
            row = db.execute('SELECT * FROM training_runs WHERE id=?', (ident,)).fetchone()
            if row is None:
                raise ValueError('Training run does not exist')
            if row['status'] in TERMINAL:
                raise ValueError('Training run is terminal and cannot be changed')
            allowed = ({'running', 'failed', 'cancelled', 'interrupted'} if row['status'] == 'queued'
                       else TERMINAL)
            if status not in allowed:
                raise ValueError(f"Invalid training run transition from {row['status']} to {status}")
            encoded_report = row['report'] if report is None else json.dumps(report, ensure_ascii=False, allow_nan=False)
            db.execute('UPDATE training_runs SET status=?,report=?,error=?,updated=? WHERE id=?',
                       (status, encoded_report, error, now(), ident))
        return self.run(ident)

    def mark_interrupted(self):
        with self.store.connection() as db:
            cursor = db.execute("UPDATE training_runs SET status='interrupted', error=?, updated=? WHERE status IN ('queued','running')",
                                ('Training was interrupted before completion', now()))
            return cursor.rowcount

    def run_directory(self, ident):
        if not isinstance(ident, str) or not re.fullmatch(r'[a-f0-9]{32}', ident):
            raise ValueError('Invalid training run ID')
        return self.store.directory / 'training' / 'runs' / ident
