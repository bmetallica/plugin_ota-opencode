from gedaechtnis.filter import geheimnis


def test_erkennt_zugangsdaten():
    for text in ("ghp_" + "Ab1" * 12, "sk-proj-" + "x1Y2" * 8, "AKIAIOSFODNN7EXAMPLE",
                 "token: 8f7Hk2LmQ9pR3sT6", "postgres://ota:Sehr-geheim@db/ota",
                 "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"):
        assert geheimnis(text), text


def test_laesst_normales_durch():
    for text in ("Build mit make test-fast, nicht make test",
                 "Commit 3f2a9c1e8b7d6a5f4e3d2c1b0a9f8e7d6c5b4a39 hat es gebrochen",
                 "Pfad /usr/share/ota-opencode/plugins/ota-sitzung/index.js",
                 "Das Passwort-Feld im Formular validieren",
                 "UUID 8f2c1d3e-4b5a-6c7d-8e9f-0a1b2c3d4e5f gehört zum Test"):
        assert geheimnis(text) is None, text
