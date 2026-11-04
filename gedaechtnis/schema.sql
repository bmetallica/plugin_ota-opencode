-- Datenbankschema des Gedächtnisses. Wird beim Start eingespielt, wenn
-- `schema_stand` kleiner ist als die Nummer eines Abschnitts (db.py).
--
-- Die harte Grenze zwischen Menschen setzt die Datenbank selbst durch:
-- Row Level Security auf allem, was Inhalte trägt. Jede Anfrage setzt
-- `ota.mensch` aus dem geprüften Token bzw. der signierten Kopfzeile; ein
-- vergessenes WHERE öffnet so keine fremden Daten. Nur ausdrücklich
-- gekennzeichnete Wege (Pflege, Löschen eines Kontos, Statistik) setzen
-- `ota.system`.

-- @abschnitt 1
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE einstellung (
  schluessel text PRIMARY KEY,
  wert       jsonb NOT NULL
);

CREATE TABLE mensch (
  id        uuid PRIMARY KEY,
  name      text NOT NULL DEFAULT '',
  gesperrt  boolean NOT NULL DEFAULT false,
  angelegt  timestamptz NOT NULL DEFAULT now()
);

-- Ein Token je Arbeitsplatz. Gespeichert wird nur der Hash.
CREATE TABLE token (
  hash              bytea PRIMARY KEY,
  mensch            uuid NOT NULL REFERENCES mensch(id) ON DELETE CASCADE,
  arbeitsplatz      uuid NOT NULL,
  arbeitsplatz_name text NOT NULL DEFAULT '',
  ausgestellt       timestamptz NOT NULL DEFAULT now(),
  widerrufen        timestamptz
);
CREATE INDEX token_arbeitsplatz ON token (arbeitsplatz);

-- Eine opencode-Sitzung (ihre Wurzel: Kind-Sitzungen und Forks bildet das
-- Plugin ota-sitzung in OTA auf die Wurzel ab).
CREATE TABLE sitzung (
  mensch            uuid NOT NULL REFERENCES mensch(id) ON DELETE CASCADE,
  kennung           text NOT NULL,
  titel             text NOT NULL DEFAULT '',
  -- Ein Titel, den der Mensch in OTA vergeben hat. Ändert nichts in opencode.
  eigener_titel     text,
  verzeichnis       text NOT NULL DEFAULT '',
  arbeitsplatz      uuid,
  arbeitsplatz_name text NOT NULL DEFAULT '',
  angelegt          timestamptz NOT NULL DEFAULT now(),
  zuletzt_aktiv     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (mensch, kennung)
);

CREATE TABLE eintrag (
  id              uuid PRIMARY KEY,
  mensch          uuid NOT NULL,
  sitzung         text NOT NULL,
  art             text NOT NULL,
  inhalt          text NOT NULL,
  schlagworte     text[] NOT NULL DEFAULT '{}',
  angeheftet      boolean NOT NULL DEFAULT false,
  -- Ohne feste Dimension: Ein anderes Embedding-Modell braucht keine
  -- Migration. Gesucht wird nur unter Einträgen mit dem aktuellen Modell.
  einbettung      vector,
  modell          text,
  such_text       tsvector GENERATED ALWAYS AS (
                    to_tsvector('german', inhalt) || to_tsvector('simple', inhalt)
                  ) STORED,
  angelegt        timestamptz NOT NULL DEFAULT now(),
  geaendert       timestamptz NOT NULL DEFAULT now(),
  zuletzt_genutzt timestamptz,
  genutzt         integer NOT NULL DEFAULT 0,
  quelle          text NOT NULL,
  FOREIGN KEY (mensch, sitzung) REFERENCES sitzung(mensch, kennung)
    ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX eintrag_sitzung ON eintrag (mensch, sitzung);
CREATE INDEX eintrag_such_text ON eintrag USING gin (such_text);

-- Was geschah, nie der Inhalt.
CREATE TABLE protokoll (
  zeit     timestamptz NOT NULL DEFAULT now(),
  mensch   uuid,
  aktion   text NOT NULL,
  sitzung  text,
  eintrag  uuid,
  anzahl   integer
);
CREATE INDEX protokoll_zeit ON protokoll (zeit);

ALTER TABLE sitzung ENABLE ROW LEVEL SECURITY;
ALTER TABLE sitzung FORCE ROW LEVEL SECURITY;
ALTER TABLE eintrag ENABLE ROW LEVEL SECURITY;
ALTER TABLE eintrag FORCE ROW LEVEL SECURITY;
ALTER TABLE token ENABLE ROW LEVEL SECURITY;
ALTER TABLE token FORCE ROW LEVEL SECURITY;

CREATE POLICY nur_eigene ON sitzung USING (
  mensch::text = current_setting('ota.mensch', true)
  OR current_setting('ota.system', true) = 'an');
CREATE POLICY nur_eigene ON eintrag USING (
  mensch::text = current_setting('ota.mensch', true)
  OR current_setting('ota.system', true) = 'an');
-- Tokens prüft die Anmeldung, bevor sie weiß, wer es ist — das ist ein
-- Systemweg. Sonst sieht jeder nur seine eigenen.
CREATE POLICY nur_eigene ON token USING (
  mensch::text = current_setting('ota.mensch', true)
  OR current_setting('ota.system', true) = 'an');

-- @abschnitt 2
-- Zugriff einer Sitzung auf das Gedächtnis einer anderen desselben Menschen:
-- `sitzung` liest zusätzlich die Einträge von `quelle`. Nur lesen, nur direkt
-- (nicht weiter über deren Zugriffe), vom Menschen in der Ansicht vergeben.
CREATE TABLE zugriff (
  mensch    uuid NOT NULL,
  sitzung   text NOT NULL,
  quelle    text NOT NULL,
  angelegt  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (mensch, sitzung, quelle),
  CHECK (sitzung <> quelle),
  FOREIGN KEY (mensch, sitzung) REFERENCES sitzung(mensch, kennung) ON DELETE CASCADE ON UPDATE CASCADE,
  FOREIGN KEY (mensch, quelle) REFERENCES sitzung(mensch, kennung) ON DELETE CASCADE ON UPDATE CASCADE
);
ALTER TABLE zugriff ENABLE ROW LEVEL SECURITY;
ALTER TABLE zugriff FORCE ROW LEVEL SECURITY;
CREATE POLICY nur_eigene ON zugriff USING (
  mensch::text = current_setting('ota.mensch', true)
  OR current_setting('ota.system', true) = 'an');

-- @abschnitt 3
-- Dokumente je Mensch: einmal hochgeladen und verarbeitet, je Sitzung
-- freigegeben oder für alle. Das Original liegt hier, damit es in OTAs
-- Sicherung ist und mit dem Konto verschwindet.
CREATE TABLE dokument (
  id          uuid PRIMARY KEY,
  mensch      uuid NOT NULL REFERENCES mensch(id) ON DELETE CASCADE,
  name        text NOT NULL,
  typ         text NOT NULL,
  groesse     integer NOT NULL,
  hash        bytea NOT NULL,
  original    bytea NOT NULL,
  -- wartet | liest | fertig | fehler
  status      text NOT NULL DEFAULT 'wartet',
  fehler      text,
  zeichen     integer NOT NULL DEFAULT 0,
  geschwaerzt integer NOT NULL DEFAULT 0,
  fuer_alle   boolean NOT NULL DEFAULT false,
  angelegt    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (mensch, hash)
);
CREATE INDEX dokument_status ON dokument (status) WHERE status IN ('wartet', 'liest');

CREATE TABLE dokument_abschnitt (
  id         bigserial PRIMARY KEY,
  mensch     uuid NOT NULL,
  dokument   uuid NOT NULL REFERENCES dokument(id) ON DELETE CASCADE,
  nummer     integer NOT NULL,
  quelle     text NOT NULL DEFAULT '',
  inhalt     text NOT NULL,
  einbettung vector,
  modell     text,
  such_text  tsvector GENERATED ALWAYS AS (
               to_tsvector('german', inhalt) || to_tsvector('simple', inhalt)
             ) STORED
);
CREATE INDEX dokument_abschnitt_dok ON dokument_abschnitt (dokument, nummer);
CREATE INDEX dokument_abschnitt_such ON dokument_abschnitt USING gin (such_text);
CREATE INDEX dokument_abschnitt_offen ON dokument_abschnitt (id) WHERE einbettung IS NULL;

CREATE TABLE dokument_freigabe (
  mensch   uuid NOT NULL,
  sitzung  text NOT NULL,
  dokument uuid NOT NULL REFERENCES dokument(id) ON DELETE CASCADE,
  angelegt timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (mensch, sitzung, dokument),
  FOREIGN KEY (mensch, sitzung) REFERENCES sitzung(mensch, kennung) ON DELETE CASCADE ON UPDATE CASCADE
);

ALTER TABLE dokument ENABLE ROW LEVEL SECURITY;
ALTER TABLE dokument FORCE ROW LEVEL SECURITY;
ALTER TABLE dokument_abschnitt ENABLE ROW LEVEL SECURITY;
ALTER TABLE dokument_abschnitt FORCE ROW LEVEL SECURITY;
ALTER TABLE dokument_freigabe ENABLE ROW LEVEL SECURITY;
ALTER TABLE dokument_freigabe FORCE ROW LEVEL SECURITY;
CREATE POLICY nur_eigene ON dokument USING (
  mensch::text = current_setting('ota.mensch', true)
  OR current_setting('ota.system', true) = 'an');
CREATE POLICY nur_eigene ON dokument_abschnitt USING (
  mensch::text = current_setting('ota.mensch', true)
  OR current_setting('ota.system', true) = 'an');
CREATE POLICY nur_eigene ON dokument_freigabe USING (
  mensch::text = current_setting('ota.mensch', true)
  OR current_setting('ota.system', true) = 'an');
