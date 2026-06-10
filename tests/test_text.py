from app.text import concise_title, exactly_30_words, parse_date, words


def test_summary_is_exactly_30_words():
    summary = exactly_30_words("Se publica una reforma fiscal relevante.")

    assert len(words(summary)) == 30
    assert summary.endswith(".")


def test_long_summary_is_truncated_to_30_words():
    summary = exactly_30_words(" ".join(f"palabra{i}" for i in range(50)))

    assert len(words(summary)) == 30
    assert "palabra29" in summary
    assert "palabra30" not in summary


def test_title_is_limited():
    title = concise_title(" ".join(f"concepto{i}" for i in range(20)))

    assert len(words(title)) == 12


def test_parses_spanish_date():
    assert parse_date("miércoles 10 de junio de 2026").isoformat() == "2026-06-10"

