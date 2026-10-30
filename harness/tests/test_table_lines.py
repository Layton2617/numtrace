from harness.tools import parse_table_lines

DOC = ("Consolidated Balance Sheets\n"
       "Current portion of debt | 2025: $5,958 | 2024: $4,907\n"
       "Brian C. Cornell | Executive contributions in last FY: $132,497 | Aggregate balance at last FYE: $8,005,585\n"
       "Capital expenditures | 2025: ( 11,750 ) | 2024: —\n"
       "Long-term debt, net of current portion ........ 4,120\n")


def test_pipe_rows_give_one_line_per_cell_with_header_in_label():
    ls = {(ln.label, ln.value) for ln in parse_table_lines(DOC)}
    assert ("Current portion of debt, 2025", 5958) in ls and ("Current portion of debt, 2024", 4907) in ls
    assert ("Brian C. Cornell, Aggregate balance at last FYE", 8_005_585) in ls
    assert ("Capital expenditures, 2025", -11_750) in ls and ("Capital expenditures, 2024", 0) in ls
    assert ("Long-term debt, net of current portion", 4120) in ls


def test_cell_span_runs_from_line_start_to_cell_end():
    bal = next(ln for ln in parse_table_lines(DOC) if ln.label.endswith("Aggregate balance at last FYE"))
    assert DOC[bal.start:bal.end] == bal.text
    assert bal.text.startswith("Brian C. Cornell |") and bal.text.endswith("$8,005,585")
    assert bal.text.rfind(bal.number_text) == len(bal.text) - len(bal.number_text)


def test_year_header_rows_are_not_numbers():
    doc = "Benefit obligation (in millions) | 2025 | 2024\nBenefit obligation | 2025: $5,097 | 2024: $4,812\n"
    assert [(ln.label, ln.value) for ln in parse_table_lines(doc)] == [
        ("Benefit obligation, 2025", 5097), ("Benefit obligation, 2024", 4812)]
