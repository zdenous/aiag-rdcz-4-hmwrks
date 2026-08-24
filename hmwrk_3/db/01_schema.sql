-- Úkol 3 - schéma malé městské knihovny (SQLite).
--
-- Katalog je záměrně "knihovnický": u autora vede jen jméno a zemi, žádnou
-- biografii. Otázky typu "kdo z nich dostal Nobelovu cenu" nebo "kteří se
-- narodili v 19. století" tedy z databáze zodpovědět nejdou - na to musí
-- agent sáhnout po nástroji na Wikipedii a data spojit sám.

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS recenze_fts;
DROP TABLE IF EXISTS recenze;
DROP TABLE IF EXISTS vypujcky;
DROP TABLE IF EXISTS ctenari;
DROP TABLE IF EXISTS knihy;
DROP TABLE IF EXISTS autori;

CREATE TABLE autori (
    id     INTEGER PRIMARY KEY,
    jmeno  TEXT NOT NULL UNIQUE,
    zeme   TEXT NOT NULL              -- země, pod kterou je autor veden v katalogu
);

CREATE TABLE knihy (
    id           INTEGER PRIMARY KEY,
    nazev        TEXT    NOT NULL,
    autor_id     INTEGER NOT NULL REFERENCES autori(id),
    rok_vydani   INTEGER NOT NULL,    -- rok vydání konkrétního výtisku, ne prvního vydání
    zanr         TEXT    NOT NULL,
    jazyk        TEXT    NOT NULL,    -- jazyk výtisku (cs/en)
    pocet_stran  INTEGER NOT NULL,
    cena_kc      REAL    NOT NULL,    -- pořizovací cena výtisku
    exemplaru    INTEGER NOT NULL     -- kolik kusů knihovna vlastní
);

CREATE TABLE ctenari (
    id             INTEGER PRIMARY KEY,
    jmeno          TEXT NOT NULL,
    email          TEXT NOT NULL UNIQUE,
    mesto          TEXT NOT NULL,
    prukaz         TEXT NOT NULL CHECK (prukaz IN ('student', 'dospely', 'senior')),
    registrace_od  TEXT NOT NULL      -- ISO datum
);

CREATE TABLE vypujcky (
    id           INTEGER PRIMARY KEY,
    kniha_id     INTEGER NOT NULL REFERENCES knihy(id),
    ctenar_id    INTEGER NOT NULL REFERENCES ctenari(id),
    datum_od     TEXT NOT NULL,       -- kdy si čtenář knihu půjčil
    datum_do     TEXT NOT NULL,       -- do kdy ji má vrátit
    vraceno_dne  TEXT,                -- NULL = ještě nevrácená
    pokuta_kc    REAL NOT NULL DEFAULT 0
);

CREATE TABLE recenze (
    id         INTEGER PRIMARY KEY,
    kniha_id   INTEGER NOT NULL REFERENCES knihy(id),
    ctenar_id  INTEGER NOT NULL REFERENCES ctenari(id),
    hodnoceni  INTEGER NOT NULL CHECK (hodnoceni BETWEEN 1 AND 5),
    datum      TEXT NOT NULL,
    text       TEXT NOT NULL
);

CREATE INDEX idx_knihy_autor     ON knihy (autor_id);
CREATE INDEX idx_vypujcky_kniha  ON vypujcky (kniha_id);
CREATE INDEX idx_vypujcky_ctenar ON vypujcky (ctenar_id);
CREATE INDEX idx_recenze_kniha   ON recenze (kniha_id);

-- Fulltextový index nad texty recenzí (FTS5, unicode61 + odstranění diakritiky,
-- takže "prekladatel" najde i "překladatel"). content='recenze' znamená, že se
-- text neduplikuje - FTS drží jen index a obsah dotahuje z původní tabulky.
CREATE VIRTUAL TABLE recenze_fts USING fts5 (
    text,
    content='recenze',
    content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);
