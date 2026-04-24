from confluence_publisher.models import DiagramBlock, slugify


def test_slugify_keeps_readable_ascii_identifiers():
    assert slugify("00 Overview.md") == "00-overview-md"
    assert slugify("Deployment & CI") == "deployment-ci"


def test_diagram_attachment_name_is_stable_and_hash_based():
    block = DiagramBlock(
        ordinal=1,
        page_slug="00-overview",
        source="graph LR\n    A --> B\n",
        output_format="svg",
    )

    assert block.hash8 == "cac7f0b9"
    assert block.attachment_name == "diagram-00-overview-01-cac7f0b9.svg"


def test_diagram_hash_normalizes_crlf_to_lf():
    lf_block = DiagramBlock(
        ordinal=1,
        page_slug="00-overview",
        source="graph LR\n    A --> B\n",
        output_format="svg",
    )
    crlf_block = DiagramBlock(
        ordinal=1,
        page_slug="00-overview",
        source="graph LR\r\n    A --> B\r\n",
        output_format="svg",
    )

    assert crlf_block.hash8 == lf_block.hash8
