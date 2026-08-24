# -*- coding: utf-8 -*-
"""
Generátor ukázkových dat knihovny - vypíše obsah db/02_seed.sql na standardní výstup.

    uv run scripts/gen_seed.py > db/02_seed.sql

Hotový seed je v repozitáři, takže tenhle skript spouštět nemusíš; je tu proto,
aby bylo vidět, odkud se data vzala, a aby šla přegenerovat. Vše je deterministické
(seed 42) a "dnešek" je natvrdo 24. 8. 2026 - stejný vstup dá vždycky stejný
výstup. Autoři a názvy knih jsou skuteční (aby je šlo dohledat na Wikipedii),
čtenáři, výpůjčky i recenze jsou vymyšlení.
"""
import random, datetime as dt

R = random.Random(42)
DNES = dt.date(2026, 8, 24)

AUTORI = [
    ("Karel Čapek", "Česko"), ("Bohumil Hrabal", "Česko"), ("Milan Kundera", "Česko"),
    ("Jaroslav Hašek", "Česko"), ("Božena Němcová", "Česko"), ("Ota Pavel", "Česko"),
    ("Alena Mornštajnová", "Česko"), ("Jáchym Topol", "Česko"), ("Vladislav Vančura", "Česko"),
    ("Fjodor Michajlovič Dostojevskij", "Rusko"), ("Lev Nikolajevič Tolstoj", "Rusko"),
    ("Jane Austenová", "Velká Británie"), ("George Orwell", "Velká Británie"),
    ("Gabriel García Márquez", "Kolumbie"), ("Haruki Murakami", "Japonsko"),
    ("Ursula K. Le Guinová", "USA"), ("Toni Morrisonová", "USA"), ("Umberto Eco", "Itálie"),
    ("Isaac Asimov", "USA"), ("Agatha Christie", "Velká Británie"),
    ("Astrid Lindgrenová", "Švédsko"), ("Stanisław Lem", "Polsko"),
    ("Olga Tokarczuková", "Polsko"), ("Kazuo Ishiguro", "Velká Británie"),
    ("Ernest Hemingway", "USA"), ("Selma Lagerlöfová", "Švédsko"),
    ("Doris Lessingová", "Velká Británie"), ("Terry Pratchett", "Velká Británie"),
]
A = {jmeno: i + 1 for i, (jmeno, _) in enumerate(AUTORI)}

# (název, autor, žánr)
KNIHY = [
    ("Válka s mloky", "Karel Čapek", "sci-fi"),
    ("R.U.R.", "Karel Čapek", "drama"),
    ("Bílá nemoc", "Karel Čapek", "drama"),
    ("Povídky z jedné kapsy", "Karel Čapek", "povídky"),
    ("Krakatit", "Karel Čapek", "sci-fi"),
    ("Ostře sledované vlaky", "Bohumil Hrabal", "novela"),
    ("Postřižiny", "Bohumil Hrabal", "novela"),
    ("Obsluhoval jsem anglického krále", "Bohumil Hrabal", "román"),
    ("Nesnesitelná lehkost bytí", "Milan Kundera", "román"),
    ("Žert", "Milan Kundera", "román"),
    ("Kniha smíchu a zapomnění", "Milan Kundera", "román"),
    ("Osudy dobrého vojáka Švejka", "Jaroslav Hašek", "román"),
    ("Babička", "Božena Němcová", "román"),
    ("Divá Bára", "Božena Němcová", "novela"),
    ("Smrt krásných srnců", "Ota Pavel", "povídky"),
    ("Jak jsem potkal ryby", "Ota Pavel", "povídky"),
    ("Hana", "Alena Mornštajnová", "román"),
    ("Tiché roky", "Alena Mornštajnová", "román"),
    ("Listopád", "Alena Mornštajnová", "román"),
    ("Sestra", "Jáchym Topol", "román"),
    ("Citlivý člověk", "Jáchym Topol", "román"),
    ("Rozmarné léto", "Vladislav Vančura", "novela"),
    ("Markéta Lazarová", "Vladislav Vančura", "historický"),
    ("Zločin a trest", "Fjodor Michajlovič Dostojevskij", "román"),
    ("Idiot", "Fjodor Michajlovič Dostojevskij", "román"),
    ("Bratři Karamazovi", "Fjodor Michajlovič Dostojevskij", "román"),
    ("Vojna a mír", "Lev Nikolajevič Tolstoj", "historický"),
    ("Anna Kareninová", "Lev Nikolajevič Tolstoj", "román"),
    ("Pýcha a předsudek", "Jane Austenová", "román"),
    ("Rozum a cit", "Jane Austenová", "román"),
    ("1984", "George Orwell", "sci-fi"),
    ("Farma zvířat", "George Orwell", "novela"),
    ("Sto roků samoty", "Gabriel García Márquez", "román"),
    ("Láska za časů cholery", "Gabriel García Márquez", "román"),
    ("Norské dřevo", "Haruki Murakami", "román"),
    ("Kafka na pobřeží", "Haruki Murakami", "román"),
    ("1Q84", "Haruki Murakami", "román"),
    ("Levá ruka tmy", "Ursula K. Le Guinová", "sci-fi"),
    ("Čaroděj Zeměmoří", "Ursula K. Le Guinová", "fantasy"),
    ("Milovaná", "Toni Morrisonová", "román"),
    ("Nejmodřejší oči", "Toni Morrisonová", "román"),
    ("Jméno růže", "Umberto Eco", "historický"),
    ("Foucaultovo kyvadlo", "Umberto Eco", "román"),
    ("Nadace", "Isaac Asimov", "sci-fi"),
    ("Já, robot", "Isaac Asimov", "sci-fi"),
    ("Vražda v Orient expresu", "Agatha Christie", "detektivka"),
    ("Smrt na Nilu", "Agatha Christie", "detektivka"),
    ("A pak už tam nezbyl žádný", "Agatha Christie", "detektivka"),
    ("Pipi Dlouhá punčocha", "Astrid Lindgrenová", "dětská"),
    ("Děti z Bullerbynu", "Astrid Lindgrenová", "dětská"),
    ("Ronja, dcera loupežníka", "Astrid Lindgrenová", "dětská"),
    ("Solaris", "Stanisław Lem", "sci-fi"),
    ("Kyberiáda", "Stanisław Lem", "sci-fi"),
    ("Běguni", "Olga Tokarczuková", "román"),
    ("Pravěk a jiné časy", "Olga Tokarczuková", "román"),
    ("Neopouštěj mě", "Kazuo Ishiguro", "sci-fi"),
    ("Soumrak dne", "Kazuo Ishiguro", "román"),
    ("Stařec a moře", "Ernest Hemingway", "novela"),
    ("Komu zvoní hrana", "Ernest Hemingway", "román"),
    ("Podivuhodná cesta Nilse Holgerssona Švédskem", "Selma Lagerlöfová", "dětská"),
    ("Zlatý zápisník", "Doris Lessingová", "román"),
    ("Barva kouzel", "Terry Pratchett", "fantasy"),
    ("Stráže! Stráže!", "Terry Pratchett", "fantasy"),
]

JMENA_M = ["Jan", "Petr", "Tomáš", "Martin", "Jakub", "Ondřej", "Lukáš", "David", "Filip", "Marek",
           "Vojtěch", "Adam", "Michal", "Radek", "Štěpán"]
JMENA_Z = ["Eva", "Jana", "Lucie", "Tereza", "Kateřina", "Markéta", "Veronika", "Barbora", "Anna",
           "Klára", "Hana", "Michaela", "Zuzana", "Nikola", "Alena"]
PRIJMENI_M = ["Novák", "Svoboda", "Dvořák", "Černý", "Procházka", "Kučera", "Veselý", "Horák",
              "Němec", "Marek", "Pospíšil", "Bartoš", "Král", "Beneš", "Fiala", "Sedláček",
              "Doležal", "Zeman", "Kolář", "Urban"]
MESTA = ["Brno", "Praha", "Olomouc", "Ostrava", "Zlín", "Kroměříž", "Vyškov", "Blansko",
         "Prostějov", "Boskovice"]
PRUKAZY = ["student", "dospely", "senior"]

def prechyl(prijmeni):
    """Ženský tvar příjmení (Novák -> Nováková, Černý -> Černá, Svoboda -> Svobodová)."""
    if prijmeni.endswith("ý"):
        return prijmeni[:-1] + "á"
    if prijmeni.endswith("a"):
        return prijmeni[:-1] + "ová"
    if prijmeni.endswith("ec"):       # Němec -> Němcová
        return prijmeni[:-2] + "cová"
    if prijmeni.endswith("ek"):       # Marek -> Marková, Sedláček -> Sedláčková
        return prijmeni[:-2] + "ková"
    return prijmeni + "ová"


def bez_diakritiky(s):
    tab = str.maketrans("áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ", "acdeeinorstuuyzACDEEINORSTUUYZ")
    return s.translate(tab)

def q(s):
    return "'" + s.replace("'", "''") + "'"

def datum(a, b):
    """Náhodné datum mezi dvěma daty."""
    return a + dt.timedelta(days=R.randrange((b - a).days + 1))

radky = []
radky.append("-- Úkol 3 - ukázková data knihovny. Generováno deterministicky (seed 42)")
radky.append("-- skriptem, který je popsaný v README; ruční úpravy se přepíšou.")
radky.append("")
radky.append("BEGIN;")
radky.append("")

# --- autoři ---------------------------------------------------------------
radky.append("INSERT INTO autori (id, jmeno, zeme) VALUES")
vals = [f"  ({i+1}, {q(j)}, {q(z)})" for i, (j, z) in enumerate(AUTORI)]
radky.append(",\n".join(vals) + ";")
radky.append("")

# --- knihy ----------------------------------------------------------------
radky.append("INSERT INTO knihy (id, nazev, autor_id, rok_vydani, zanr, jazyk, pocet_stran, cena_kc, exemplaru) VALUES")
vals = []
for i, (nazev, autor, zanr) in enumerate(KNIHY):
    kid = i + 1
    rok = R.randrange(2004, 2026)
    jazyk = "en" if kid % 11 == 0 else "cs"
    stran = R.choice([96, 128, 160, 192, 224, 256, 288, 320, 384, 448, 512, 624, 768, 912])
    cena = R.choice([149, 199, 249, 299, 349, 399, 449, 499, 549, 649, 799])
    ex = R.choice([1, 1, 2, 2, 2, 3, 3, 4, 5])
    vals.append(f"  ({kid}, {q(nazev)}, {A[autor]}, {rok}, {q(zanr)}, {q(jazyk)}, {stran}, {cena}, {ex})")
radky.append(",\n".join(vals) + ";")
radky.append("")

# --- čtenáři --------------------------------------------------------------
POCET_CTENARU = 40
ctenari = []
pouzite = set()
for i in range(POCET_CTENARU):
    zena = i % 2 == 1
    krestni = R.choice(JMENA_Z if zena else JMENA_M)
    prijmeni = R.choice(PRIJMENI_M)
    if zena:
        prijmeni = prechyl(prijmeni)
    jmeno = f"{krestni} {prijmeni}"
    while jmeno in pouzite:
        krestni = R.choice(JMENA_Z if zena else JMENA_M)
        jmeno = f"{krestni} {prijmeni}"
    pouzite.add(jmeno)
    email = bez_diakritiky(f"{krestni}.{prijmeni}".lower()) + "@example.cz"
    ctenari.append((i + 1, jmeno, email, R.choice(MESTA), R.choice(PRUKAZY),
                    datum(dt.date(2019, 1, 1), dt.date(2026, 6, 30)).isoformat()))
radky.append("INSERT INTO ctenari (id, jmeno, email, mesto, prukaz, registrace_od) VALUES")
radky.append(",\n".join(
    f"  ({i}, {q(j)}, {q(e)}, {q(m)}, {q(p)}, {q(r)})" for i, j, e, m, p, r in ctenari) + ";")
radky.append("")

# --- výpůjčky -------------------------------------------------------------
# Populární tituly se půjčují častěji - váha klesá s pořadím v katalogu,
# aby žebříčky nejpůjčovanějších knih nebyly plochý šum.
POCET_VYPUJCEK = 300
vahy = [max(1, 30 - (kid * 29) // len(KNIHY)) for kid in range(len(KNIHY))]
vypujcky = []
for i in range(POCET_VYPUJCEK):
    kniha = R.choices(range(1, len(KNIHY) + 1), weights=vahy)[0]
    ctenar = R.randrange(1, POCET_CTENARU + 1)
    od = datum(dt.date(2025, 1, 2), dt.date(2026, 8, 20))
    do = od + dt.timedelta(days=30)
    # 12 % výpůjček je pořád venku, zbytek je vrácený (někdy po termínu)
    if R.random() < 0.12 and od > dt.date(2026, 5, 1):
        vraceno = None
        dnu_po = max(0, (DNES - do).days)
    else:
        posun = R.choice([-21, -14, -9, -5, -3, -1, 0, 2, 6, 13, 25])
        vraceno_d = min(od + dt.timedelta(days=30 + posun), DNES)
        vraceno = vraceno_d.isoformat()
        dnu_po = max(0, (vraceno_d - do).days)
    pokuta = min(dnu_po * 3.0, 300.0)
    vypujcky.append((i + 1, kniha, ctenar, od.isoformat(), do.isoformat(), vraceno, pokuta))
radky.append("INSERT INTO vypujcky (id, kniha_id, ctenar_id, datum_od, datum_do, vraceno_dne, pokuta_kc) VALUES")
radky.append(",\n".join(
    f"  ({i}, {k}, {c}, {q(o)}, {q(d)}, {q(v) if v else 'NULL'}, {p})"
    for i, k, c, o, d, v, p in vypujcky) + ";")
radky.append("")

# --- recenze --------------------------------------------------------------
# Ručně psané recenze k jasně vyhledatelným tématům (překlad, poškozené výtisky,
# audiokniha, čtenářský klub) - na nich se dá ukázat fulltext.
RUCNI = [
    (23, 5, 5, "Markéta Lazarová v tomhle vydání má nádhernou vazbu, ale písmo je nepříjemně malé. Jinak text sám je klenot."),
    (24, 5, 5, "Nový překlad Zločinu a trestu je o poznání čtivější než ten starý; překladatelka odvedla skvělou práci."),
    (26, 2, 2, "Bratři Karamazovi - výtisk je poškozený, chybí strany 210 až 226. Prosím o výměnu, jinak by to bylo za pět hvězd."),
    (31, 5, 4, "Poslouchal jsem 1984 jako audioknihu a pak si to přečetl ještě jednou. Namluvené to je výborně, ale tištěná verze má lepší poznámky."),
    (33, 4, 4, "Sto roků samoty jsme četli ve čtenářském klubu a debata byla nejlepší za celý rok. Doporučuji do klubu i ostatním."),
    (35, 3, 3, "Překlad Norského dřeva mi místy skřípal, některé věty působí strojově. Příběh ale funguje."),
    (42, 5, 5, "Jméno růže je detektivka i historický esej v jednom. Poznámkový aparát je rozsáhlý, na první čtení zbytečně."),
    (49, 5, 5, "Pipi Dlouhá punčocha je ideální na předčítání dětem, dcera se smála nahlas. Ilustrace jsou barevné a velké."),
    (50, 4, 4, "Děti z Bullerbynu čteme dětem před spaním, kapitoly mají akorát délku."),
    (52, 4, 4, "Solaris je pomalý rozjezd, prvních padesát stran mi trvalo. Od poloviny ale nešel odložit."),
    (56, 5, 5, "Neopouštěj mě má nenápadný začátek a naprosto zdrcující závěr. Dlouho mi to leželo v hlavě."),
    (58, 2, 2, "Stařec a moře - výtisk byl vlhký a stránky se lepily. Škoda, obsah je klasika."),
    (62, 4, 4, "Barva kouzel je vtipná, ale humor hodně stojí na překladu; Kantůrkovy poznámky pod čarou jsou samostatný zážitek."),
    (17, 5, 5, "Hana je nejsilnější česká kniha, co jsem za poslední roky četla. Doporučuji do čtenářského klubu."),
    (12, 3, 3, "Švejk je klasika, ale tenhle výtisk je dost opotřebovaný, hřbet drží na lepence."),
    (37, 2, 2, "1Q84 je zbytečně dlouhé, třetí díl se vleče a závěr nic nevysvětlí."),
]
FRAZE_DOBRE = [
    "Skvěle napsané, přečteno za dva večery.",
    "Postavy jsou uvěřitelné a dialogy sedí.",
    "Vracím se k tomu už podruhé a pořád to funguje.",
    "Atmosféra knihy je hutná a drží pohromadě až do konce.",
    "Jazyk je krásný, některé věty jsem si podtrhával.",
    "Konečně kniha, u které se člověk nenudí.",
]
FRAZE_STREDNI = [
    "Dobré, ale nic převratného.",
    "První polovina je silnější než druhá.",
    "Čte se rychle, ale za měsíc si z toho nic nepamatuju.",
    "Téma zajímavé, zpracování průměrné.",
    "Očekával jsem víc, přesto to není propadák.",
]
FRAZE_SPATNE = [
    "Nedočetl jsem, styl mi vůbec nesedl.",
    "Zdlouhavé a plné odboček, které nikam nevedou.",
    "Postavy mi zůstaly cizí, nezaujalo mě to.",
    "Na konci mi to přišlo nedotažené.",
]
recenze = []
for kniha, ctenar, hodnoceni, text in RUCNI:
    recenze.append((kniha, ctenar, hodnoceni, text))
POCET_RECENZI = 150
for _ in range(POCET_RECENZI - len(RUCNI)):
    kniha = R.choices(range(1, len(KNIHY) + 1), weights=vahy)[0]
    ctenar = R.randrange(1, POCET_CTENARU + 1)
    h = R.choices([1, 2, 3, 4, 5], weights=[3, 7, 18, 37, 35])[0]
    pool = FRAZE_DOBRE if h >= 4 else (FRAZE_STREDNI if h == 3 else FRAZE_SPATNE)
    text = " ".join(R.sample(pool, 2))
    recenze.append((kniha, ctenar, h, text))
R.shuffle(recenze)
vals = [(i + 1, kniha, ctenar, h, text)
        for i, (kniha, ctenar, h, text) in enumerate(recenze)]
radky.append("INSERT INTO recenze (id, kniha_id, ctenar_id, hodnoceni, datum, text) VALUES")
radky.append(",\n".join(
    f"  ({i}, {k}, {c}, {h}, {q(datum(dt.date(2025, 2, 1), DNES).isoformat())}, {q(t)})"
    for i, k, c, h, t in vals) + ";")
radky.append("")
radky.append("COMMIT;")

print("\n".join(radky))
