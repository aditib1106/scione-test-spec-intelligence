"""Invented pilot identities and lab declarations; never client capability data."""

from decimal import Decimal

from scione.catalogs.models import (
    Alias,
    CanonicalTest,
    LabCapability,
    LabProfile,
    ReferenceCatalog,
    TestMethod,
)


def load_reference_catalog() -> ReferenceCatalog:
    """Return a fresh pilot catalog without reading files or calling a model.

    Test names and method identifiers are scoped to the supplied MS-PNT-410
    demonstration. Chinese aliases are curated demo terminology. Editions are
    deliberately unknown: no method text, editions, or global equivalences are
    inferred from identifier similarity or sample-specific regional allowances.
    """

    method_rows = [
        ("iso_2409", "ISO 2409", "Cross-cut adhesion"),
        ("astm_d3359", "ASTM D3359", "Tape adhesion"),
        ("gb_t_9286", "GB/T 9286", "Cross-cut adhesion"),
        ("iso_4624", "ISO 4624", "Pull-off adhesion"),
        ("iso_2813", "ISO 2813", "Gloss measurement"),
        ("iso_15184", "ISO 15184", "Pencil hardness"),
        ("iso_2815", "ISO 2815", "Buchholz indentation"),
        ("iso_20566", "ISO 20566", "Car-wash scratch resistance"),
        ("astm_d6279", "ASTM D6279", "Dry-mar resistance"),
        ("iso_6272_1", "ISO 6272-1", "Impact resistance"),
        ("iso_20567_1", "ISO 20567-1", "Stone-chip resistance"),
        ("sae_j400", "SAE J400", "Gravelometer stone-chip resistance"),
        ("sae_j2527", "SAE J2527", "Xenon-arc weathering"),
        ("gb_t_1865", "GB/T 1865", "Accelerated weathering"),
        ("iso_16474_3", "ISO 16474-3", "Fluorescent UV exposure"),
        ("iso_9227", "ISO 9227", "Salt-spray exposure"),
        ("astm_b117", "ASTM B117", "Salt-spray exposure"),
        ("gb_t_1771", "GB/T 1771", "Salt-spray exposure"),
        ("iso_6270_2", "ISO 6270-2", "Humidity exposure"),
        ("vda_233_102", "VDA 233-102", "Cyclic corrosion"),
        ("iso_2812_5", "ISO 2812-5", "Chemical resistance"),
        ("astm_e2194", "ASTM E2194", "Multi-angle color measurement"),
        ("iso_11664_4", "ISO 11664-4", "CIELAB colorimetry"),
        ("din_6175_2", "DIN 6175-2", "Color-tolerance assessment"),
    ]
    methods = [
        TestMethod(
            id=method_id,
            identifier=identifier,
            name=name,
            edition=None,
            aliases=[Alias(text=identifier, language="und")],
        )
        for method_id, identifier, name in method_rows
    ]
    # Associations do not encode alternatives, replacements, or regional acceptance.
    test_rows = [
        (
            "cross_cut_adhesion",
            "Cross-cut adhesion",
            "划格附着力",
            ["Cross cut adhesion", "Cross-hatch adhesion"],
            ["iso_2409", "astm_d3359", "gb_t_9286"],
        ),
        ("pull_off_adhesion", "Pull-off adhesion", "拉开法附着力", [], ["iso_4624"]),
        ("gloss", "Gloss", "光泽度", ["Gloss measurement"], ["iso_2813"]),
        ("pencil_hardness", "Pencil hardness", "铅笔硬度", ["Pencil-hardness test"], ["iso_15184"]),
        ("buchholz_indentation", "Buchholz indentation", "巴克霍尔兹压痕", [], ["iso_2815"]),
        (
            "scratch_resistance",
            "Scratch resistance",
            "耐划伤性",
            ["Mar resistance"],
            ["iso_20566", "astm_d6279"],
        ),
        ("impact_resistance", "Impact resistance", "耐冲击性", [], ["iso_6272_1"]),
        (
            "stone_chip_resistance",
            "Stone-chip resistance",
            "耐石击性",
            ["Stone chip resistance"],
            ["iso_20567_1", "sae_j400"],
        ),
        (
            "xenon_arc_weathering",
            "Xenon-arc weathering",
            "氙灯老化",
            ["Xenon arc exposure"],
            ["sae_j2527", "gb_t_1865"],
        ),
        (
            "uv_fluorescent_exposure",
            "UV fluorescent exposure",
            "荧光紫外老化",
            ["Fluorescent UV exposure"],
            ["iso_16474_3"],
        ),
        (
            "neutral_salt_spray",
            "Neutral salt spray",
            "中性盐雾",
            ["Salt spray (neutral)", "NSS"],
            ["iso_9227", "astm_b117", "gb_t_1771"],
        ),
        ("humidity_resistance", "Humidity resistance", "耐湿热性", ["Humidity"], ["iso_6270_2"]),
        ("cyclic_corrosion", "Cyclic corrosion", "循环腐蚀", [], ["vda_233_102"]),
        (
            "chemical_resistance",
            "Chemical resistance",
            "耐化学品性",
            ["Chemical and fluid resistance"],
            ["iso_2812_5"],
        ),
        (
            "color_difference",
            "Color difference",
            "色差",
            ["Color difference (ΔE)", "Delta E"],
            ["astm_e2194", "iso_11664_4", "din_6175_2"],
        ),
    ]
    tests = [
        CanonicalTest(
            id=test_id,
            name=name,
            description=(
                f"Pilot identity for {name.lower()}; inspect the requested method and conditions "
                "separately. This record does not contain standard requirements."
            ),
            aliases=[Alias(text=name, language="en"), Alias(text=chinese, language="zh")]
            + [Alias(text=alias, language="en") for alias in aliases],
            method_ids=method_ids,
        )
        for test_id, name, chinese, aliases, method_ids in test_rows
    ]
    return ReferenceCatalog(
        id="ms_pnt_410_pilot",
        name="MS-PNT-410 pilot test catalog",
        version=1,
        synthetic=True,
        source_note=(
            "Manually curated pilot metadata for the supplied MS-PNT-410 sample, not an "
            "authoritative standards database. Chinese aliases are demonstration terminology. "
            "Editions are unknown; method associations do not imply equivalence. "
            "No standard body text or lab capability claims are included."
        ),
        tests=tests,
        methods=methods,
    )


def default_demo_lab() -> LabProfile:
    """Return explicitly invented declarations and prices; no files are created."""

    return LabProfile(
        id="demo_lab",
        name="Demo Coatings Lab",
        description=(
            "Synthetic prototype lab. All capabilities, limitations and prices are invented "
            "for demonstration, not supplied or verified by the client. A listed method does "
            "not confirm every requested condition; unlisted tests are not proven impossible."
        ),
        capabilities=[
            LabCapability(
                id="cap_cross_cut",
                canonical_test_id="cross_cut_adhesion",
                method_ids=["iso_2409"],
                declared_constraints=(
                    "2 mm spacing on coated metal panels; bake coverage to confirm."
                ),
                price=Decimal("35.00"),
                currency="USD",
                pricing_basis="per panel",
                notes="Synthetic price; sample preparation excluded.",
            ),
            LabCapability(
                id="cap_gloss",
                canonical_test_id="gloss",
                method_ids=["iso_2813"],
                declared_constraints=(
                    "20°, 60° and 85° measurements; sample orientation to confirm."
                ),
                price=Decimal("25.00"),
                currency="USD",
                pricing_basis="per panel",
                notes="Synthetic demonstration declaration and price.",
            ),
            LabCapability(
                id="cap_pencil",
                canonical_test_id="pencil_hardness",
                method_ids=["iso_15184"],
                declared_constraints="750 g test load; coated flat panels.",
                notes="Synthetic declaration; price not supplied.",
            ),
            LabCapability(
                id="cap_stone_chip",
                canonical_test_id="stone_chip_resistance",
                method_ids=["iso_20567_1"],
                declared_constraints="23 °C only. Low-temperature testing is not declared.",
                notes="Synthetic limitation for reviewing requested conditions.",
            ),
            LabCapability(
                id="cap_salt_spray",
                canonical_test_id="neutral_salt_spray",
                method_ids=["astm_b117"],
                declared_constraints="Up to 1000 h exposure; substrate coverage to confirm.",
                notes=(
                    "Synthetic method declaration. Do not automatically substitute this method "
                    "for ISO 9227; any acceptance needs customer-specific evidence."
                ),
            ),
        ],
    )
