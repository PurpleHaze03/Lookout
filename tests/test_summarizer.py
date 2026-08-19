from lookout.summarizer import summarize_html

ARTICLE = """
<html><head><title>Solar power hits new record</title></head>
<body>
  <nav><a href="/">Home</a><a href="/news">News</a><a href="/sports">Sports</a></nav>
  <article>
    <p>Solar power generation reached a new record in Europe this summer,
       covering nearly a quarter of electricity demand during peak hours.
       Analysts attribute the record to rapidly falling panel prices and a wave
       of rooftop installations across Germany, Spain, and the Netherlands.
       Grid operators, however, warn that storage capacity has not kept pace
       with the growth in solar generation. Battery installations grew as well,
       but from a much smaller base than solar panels themselves.
       Negative electricity prices occurred on a record number of days,
       highlighting the mismatch between solar supply and demand. Policy makers
       are now debating incentives for home batteries and flexible tariffs.
       Industry groups expect the solar record to be broken again next year.
       Consumer advocates note that retail prices have not fallen as quickly.
       Several utilities announced new storage projects in response.
       The record marks a milestone in Europe's energy transition.</p>
  </article>
  <footer>Copyright 2026. Subscribe to our newsletter. Follow us everywhere.</footer>
  <script>console.log("tracking pixel");</script>
</body></html>
"""


def test_summary_respects_max_sentences():
    summary = summarize_html(ARTICLE, max_sentences=3)
    assert 0 < len(summary.sentences) <= 3


def test_title_extracted():
    assert summarize_html(ARTICLE).title == "Solar power hits new record"


def test_boilerplate_excluded():
    text = summarize_html(ARTICLE, max_sentences=12).text
    assert "newsletter" not in text.lower()
    assert "tracking pixel" not in text
    assert "Sports" not in text


def test_summary_keeps_original_order():
    summary = summarize_html(ARTICLE, max_sentences=4)
    positions = [ARTICLE.find(s[:40]) for s in summary.sentences]
    assert positions == sorted(positions)
    assert all(p != -1 for p in positions)


def test_key_topic_present():
    summary = summarize_html(ARTICLE, max_sentences=3)
    assert "solar" in summary.text.lower()


def test_empty_page():
    summary = summarize_html("<html><body></body></html>")
    assert summary.sentences == []
    assert summary.title == "Untitled page"


def test_short_page_returned_whole():
    html = "<html><title>T</title><body><p>This is a single short sentence here.</p></body></html>"
    summary = summarize_html(html, max_sentences=5)
    assert len(summary.sentences) == 1
