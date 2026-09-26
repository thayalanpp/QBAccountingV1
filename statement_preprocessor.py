import re


MONTHS = (
    "JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC"
)


def extract_statement_header(raw_text):
    """
    Extract:
    STATEMENT DATE
    PREVIOUS STATEMENT
    STATEMENT PERIOD
    """

    header_lines = []

    for line in raw_text.splitlines():
        clean_line = line.strip().strip("|").strip()
        upper_line = clean_line.upper()

        if "STATEMENT DATE:" in upper_line:
            header_lines.append(clean_line)

        elif "PREVIOUS STATEMENT:" in upper_line:
            header_lines.append(clean_line)

        elif "STATEMENT PERIOD:" in upper_line:
            header_lines.append(clean_line)

    return "\n".join(header_lines)


def extract_balance_summary(raw_text):
    """
    Extract everything from:

    CALCULATING YOUR BALANCE

    through:

    NEW BALANCE
    """

    lines = raw_text.splitlines()

    summary_lines = []
    in_summary = False

    for line in lines:
        clean_line = line.strip().strip("|").strip()
        upper_line = clean_line.upper()

        if "CALCULATING YOUR BALANCE" in upper_line:
            in_summary = True

        if in_summary and clean_line:
            summary_lines.append(clean_line)

        if (
            in_summary
            and "NEW BALANCE" in upper_line
        ):
            break

    return "\n".join(summary_lines)


def normalize_amount_in_line(line):
    """
    Convert the final transaction amount:

    -$28.24     -> -28.24
    $28.24      -> 28.24
    -$10,424.31 -> -10424.31
    $5,230.38   -> 5230.38

    The original +/- sign is preserved.
    """

    amount_pattern = re.compile(
        r'(-?\$[\d,]+\.\d{2})\s*$'
    )

    match = amount_pattern.search(line)

    if not match:
        return line

    original_amount = match.group(1)

    normalized_amount = (
        original_amount
        .replace("$", "")
        .replace(",", "")
    )

    return (
        line[:match.start()]
        + normalized_amount
    ).strip()


def extract_transaction_lines(raw_text):
    """
    Keep only lines containing two transaction dates such as:

    MAR 10 MAR 11
    APR 01 APR 02

    Normalize the final amount while preserving
    the original positive or negative sign.
    """

    transaction_pattern = re.compile(
        rf"\b({MONTHS})\s+\d{{1,2}}\b"
        rf".*?"
        rf"\b({MONTHS})\s+\d{{1,2}}\b",
        re.IGNORECASE
    )

    transaction_lines = []

    for line in raw_text.splitlines():
        clean_line = line.strip().strip("|").strip()

        if transaction_pattern.search(clean_line):

            clean_line = normalize_amount_in_line(
                clean_line
            )

            transaction_lines.append(
                clean_line
            )

    return "\n".join(transaction_lines)


def build_filtered_statement(raw_text):
    """
    Build the compact statement text that will be
    sent to DeepSeek.
    """

    statement_header = extract_statement_header(
        raw_text
    )

    balance_summary = extract_balance_summary(
        raw_text
    )

    transactions = extract_transaction_lines(
        raw_text
    )

    filtered_text = f"""
### STATEMENT HEADER

{statement_header}

### CALCULATING YOUR BALANCE

{balance_summary}

### TRANSACTIONS

{transactions}
""".strip()

    return filtered_text