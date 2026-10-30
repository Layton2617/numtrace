from data_layer.filing_text import html_to_text, merge_cells

BS = """<?xml version="1.0" encoding="utf-8"?><html><body><div style="display:none"><ix:header>ctx</ix:header></div>
<div><span>Consolidated Balance </span><span>Sheets</span></div>
<table>
<tr><td>December 31 (in millions)</td><td>2025</td><td></td><td>2024</td></tr>
<tr><td>Current portion of debt</td><td>$</td><td>5,958</td><td>$</td><td>4,907</td></tr>
<tr><td>Other</td><td>(12</td><td>)</td><td>&#8212;</td><td>(1)</td></tr>
</table></body></html>"""


def test_merge_cells_joins_dollar_paren_and_drops_footnotes():
    assert merge_cells(["$", "5,958", " ", "(12", ")", "(1)", "—"]) == ["$5,958", "(12)", "—"]


def test_table_rows_carry_their_column_headers():
    lines = html_to_text(BS).split("\n")
    assert lines[0] == "Consolidated Balance Sheets"
    assert "December 31 (in millions) | 2025 | 2024" in lines
    assert "Current portion of debt | 2025: $5,958 | 2024: $4,907" in lines
    assert "Other | 2025: (12) | 2024: —" in lines
    assert "ctx" not in "\n".join(lines)


PROXY = """<html><body><table>
<tr><td>&#8203;</td><td>&#8203;</td><td>Executive</td><td>Registrant</td><td>Aggregate</td></tr>
<tr><td>&#8203;</td><td>&#8203;</td><td>Contributions</td><td>Contributions</td><td>Balance</td></tr>
<tr><td>Name</td><td>Plan</td><td>($)</td><td>($)</td><td>($)</td></tr>
<tr><td>A. Smith</td><td>DCP</td><td>N/A</td><td>N/A</td><td>N/A</td></tr>
<tr><td></td><td>Restoration Plan<sup>(2)</sup></td><td>1,000<span style="position:relative;top:-3pt">3</span></td>
<td>$&#8212;</td><td>9,000</td></tr>
</table>
<table><tr><td>Executive Contributions ($)</td><td>Aggregate Balance ($)</td></tr>
<tr><td>B. Jones</td><td>500</td><td>7,000</td></tr></table></body></html>"""


def test_proxy_table_headers_survive_stacking_spacers_and_footnote_marks():
    lines = html_to_text(PROXY).split("\n")
    # stacked header rows and a ($) row are joined; zero-width spacer cells and raised footnote marks are dropped;
    # an all-N/A row is data, not a header
    assert "A. Smith | DCP | N/A | N/A | N/A" in lines
    assert ("Restoration Plan | Executive Contributions ($): 1,000 | Registrant Contributions ($): $— | "
            "Aggregate Balance ($): 9,000") in lines
    # a header row without a cell over the label column still names every value column
    assert "B. Jones | Executive Contributions ($): 500 | Aggregate Balance ($): 7,000" in lines


# Rows that leave cells empty: the value's column comes from the grid (colspan-aware), not from the right end.
JNJ_EPS = """<html><body><table>
<tr><td></td><td colspan="2">September 28, 2025</td><td colspan="2">Percent to Sales</td><td colspan="2">September 29, 2024</td>
<td colspan="2">Percent to Sales</td></tr>
<tr><td>Cost of products sold</td><td></td><td>7,303</td><td></td><td>30.4</td><td></td><td>6,963</td><td></td><td>31.0</td></tr>
<tr><td>Basic</td><td>$</td><td>2.14</td><td></td><td></td><td>$</td><td>1.12</td><td></td><td></td></tr>
</table></body></html>"""
BA_RATINGS = """<html><body><table>
<tr><td>Rating categories</td><td>Current</td><td>2021</td><td>2020</td><td>2019</td><td>2018</td><td>Prior</td><td>Total</td></tr>
<tr><td>B</td><td></td><td>35</td><td></td><td></td><td></td><td>218</td><td>253</td></tr>
</table></body></html>"""
WMT_DEBT = """<html><body><table>
<tr><td></td><td colspan="2">January 31, 2023</td><td colspan="2">January 31, 2022</td></tr>
<tr><td></td><td>Amount</td><td>Average Rate</td><td>Amount</td><td>Average Rate</td></tr>
<tr><td>Short-term borrowings</td><td>372</td><td>4.1%</td><td>410</td><td>2.6%</td></tr>
<tr><td>Total debt</td><td>38,840</td><td></td><td>37,667</td><td></td></tr>
</table></body></html>"""
# The same short row when the header rows are not as wide: no column names rather than wrong ones.
UNEVEN = """<html><body><table>
<tr><td>September 28, 2025</td><td>Percent to Sales</td><td>September 29, 2024</td><td>Percent to Sales</td></tr>
<tr><td>Basic</td><td>$</td><td>2.14</td><td></td><td>$</td><td>1.12</td></tr>
</table></body></html>"""


def test_values_take_the_header_of_their_grid_column():
    lines = html_to_text(JNJ_EPS).split("\n")
    assert "Basic | September 28, 2025: $2.14 | September 29, 2024: $1.12" in lines
    assert ("Cost of products sold | September 28, 2025: 7,303 | Percent to Sales: 30.4 | "
            "September 29, 2024: 6,963 | Percent to Sales: 31.0") in lines
    assert "B | 2021: 35 | Prior: 218 | Total: 253" in html_to_text(BA_RATINGS).split("\n")
    wmt = html_to_text(WMT_DEBT).split("\n")
    assert "Total debt | January 31, 2023 Amount: 38,840 | January 31, 2022 Amount: 37,667" in wmt
    assert "Basic | $2.14 | $1.12" in html_to_text(UNEVEN).split("\n")

BA_ROWSPAN = """<html><body><table>
<tr><td>Name</td><td>Plan Name</td><td>Executive Contributions ($)</td><td>Aggregate Balance ($)</td></tr>
<tr><td rowspan="2">Leanne G. Caret</td><td>Executive Supplemental Savings Plan</td><td>70,652</td><td>12,374,601</td></tr>
<tr><td>Deferred RSUs</td><td>&#8212;</td><td>2,109,486</td></tr>
</table></body></html>"""
GS_RANGES = """<html><body><table>
<tr><td>$ in millions, except inputs</td><td>Amount or Range</td><td>Average/ Median</td></tr>
<tr><td>Credit, net</td><td>$1,666</td><td></td></tr>
<tr><td>Correlation</td><td>25 % to 81 %</td><td>64 %/ 63 %</td></tr>
<tr><td>Currencies, net</td><td>$125</td><td></td></tr>
</table></body></html>"""


def test_rowspan_continuation_rows_and_range_rows_keep_their_columns():
    # the name cell spans two rows: the plan row below it starts one column further right
    assert ("Deferred RSUs | Executive Contributions ($): — | Aggregate Balance ($): 2,109,486"
            in html_to_text(BA_ROWSPAN).split("\n"))
    # a row of ranges is data, not a header for the rows below it
    gs = html_to_text(GS_RANGES).split("\n")
    assert "Currencies, net | Amount or Range: $125" in gs and "Credit, net | Amount or Range: $1,666" in gs
