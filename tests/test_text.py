from app.text import bounded_summary, concise_title, parse_date, words


def test_short_summary_is_extended_to_the_minimum():
    summary = bounded_summary("Se publica una reforma fiscal relevante.")

    assert 40 <= len(words(summary)) <= 80
    assert summary.endswith(".")


def test_empty_summary_is_extended_to_the_minimum():
    summary = bounded_summary("")

    assert 40 <= len(words(summary)) <= 80
    assert summary.endswith(".")


def test_long_summary_is_truncated_to_the_maximum():
    summary = bounded_summary(" ".join(f"palabra{i}" for i in range(200)))

    assert len(words(summary)) == 80
    assert "palabra79" in summary
    assert "palabra80" not in summary
    assert summary.endswith(".")


def test_summary_in_range_is_left_at_length():
    text = " ".join(f"palabra{i}" for i in range(50))
    summary = bounded_summary(text)

    assert len(words(summary)) == 50


def test_title_is_limited():
    title = concise_title(" ".join(f"concepto{i}" for i in range(20)))

    assert len(words(title)) == 12


def test_parses_spanish_date():
    assert parse_date("miércoles 10 de junio de 2026").isoformat() == "2026-06-10"

