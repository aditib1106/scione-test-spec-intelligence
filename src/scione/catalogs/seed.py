"""Invented pilot identities and lab declarations; never client capability data."""

from decimal import Decimal

from scione.catalogs.models import (
    Alias,
    CanonicalTest,
    LabCapability,
    LabOperatingLimit,
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
        ("iso_16925", "ISO 16925", "Pressure-water-jet adhesion"),
        ("iso_2812_2", "ISO 2812-2", "Water immersion"),
        ("iso_4623_2", "ISO 4623-2", "Filiform corrosion on aluminum"),
        ("sae_j1976", "SAE J1976", "Outdoor weathering exposure"),
        ("astm_d5402", "ASTM D5402", "Solvent resistance / cure check"),
        ("vdma_24364", "VDMA 24364", "Paint-wetting impairment substances"),
        # These are source-described customer procedures, not invented standard
        # identifiers or declarations that a lab supports the procedures.
        ("customer_wave_scan", "Customer procedure: Wave-scan structure meter", "Wave-scan"),
        (
            "customer_mottling_visual",
            "Customer procedure: Visual, 0 to 5 scale (0 = none)",
            "Visual mottling / clouding assessment",
        ),
        (
            "customer_popping_wedge",
            "Customer procedure: Wedge panel, DFT at first pop",
            "Clearcoat popping-limit wedge panel",
        ),
        (
            "customer_sagging_wedge",
            "Customer procedure: Wedge panel with holes",
            "Clearcoat sagging-limit wedge panel",
        ),
        (
            "customer_craters_visual",
            "Customer procedure: Visual, 300 x 500 mm",
            "Visual craters / pinholes assessment",
        ),
        ("customer_gradient_oven", "Customer procedure: Gradient oven", "Gradient-oven procedure"),
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
    for method in methods:
        if method.id.startswith("customer_"):
            method.aliases.append(
                Alias(text=method.identifier.removeprefix("Customer procedure: "), language="en")
            )
        if method.id == "astm_d6279":
            method.aliases.append(Alias(text="Crockmeter per ASTM D6279", language="en"))
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
            ["Mar resistance", "Scratch resistance, dry mar", "Scratch resistance, car wash"],
            ["iso_20566", "astm_d6279"],
        ),
        ("impact_resistance", "Impact resistance", "耐冲击性", [], ["iso_6272_1"]),
        (
            "stone_chip_resistance",
            "Stone-chip resistance",
            "耐石击性",
            ["Stone chip resistance", "Stone-chip resistance, gravelometer"],
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
            ["iso_2812_5", "customer_gradient_oven"],
        ),
        (
            "color_difference",
            "Color difference",
            "色差",
            [
                "Color difference (ΔE)",
                "Delta E",
                "Color difference (ΔE) vs master",
                "Color difference (ΔE) after weathering",
            ],
            ["astm_e2194", "iso_11664_4", "din_6175_2"],
        ),
        ("long_wave", "Long wave (LW)", "长波", ["Long-wave structure"], ["customer_wave_scan"]),
        ("short_wave", "Short wave (SW)", "短波", ["Short-wave structure"], ["customer_wave_scan"]),
        (
            "distinctness_of_image",
            "Distinctness of image (DOI)",
            "鲜映性",
            ["DOI"],
            ["customer_wave_scan"],
        ),
        (
            "mottling_clouding",
            "Mottling / clouding",
            "发花与云斑",
            [],
            ["customer_mottling_visual"],
        ),
        (
            "popping_limit",
            "Popping limit, clearcoat",
            "清漆起泡极限",
            [],
            ["customer_popping_wedge"],
        ),
        (
            "sagging_limit",
            "Sagging limit, clearcoat",
            "清漆流挂极限",
            [],
            ["customer_sagging_wedge"],
        ),
        (
            "craters_pinholes",
            "Craters / pinholes",
            "缩孔与针孔",
            [],
            ["customer_craters_visual"],
        ),
        (
            "paint_wetting_impairment",
            "Paint-wetting impairment substances",
            "漆膜润湿干扰物质",
            ["PWIS"],
            ["vdma_24364"],
        ),
        (
            "pressure_water_jet_adhesion",
            "Pressure water jet adhesion",
            "高压水射流附着力",
            ["Pressure-water-jet adhesion"],
            ["iso_16925"],
        ),
        ("water_immersion", "Water immersion", "耐水浸泡", [], ["iso_2812_2"]),
        (
            "filiform_corrosion",
            "Filiform corrosion (aluminum)",
            "铝材丝状腐蚀",
            ["Filiform (aluminum)"],
            ["iso_4623_2"],
        ),
        ("thermal_cycling", "Thermal cycling", "温度循环", [], []),
        ("heat_aging", "Heat aging", "热老化", ["Heat ageing"], []),
        (
            "outdoor_subtropical_weathering",
            "Outdoor, subtropical",
            "亚热带户外暴露",
            ["Subtropical outdoor exposure"],
            ["sae_j1976"],
        ),
        ("environmental_etch", "Environmental etch", "环境侵蚀", [], []),
        (
            "solvent_cure_check",
            "MEK (cure check)",
            "MEK固化检查",
            ["MEK cure check"],
            ["astm_d5402"],
        ),
        ("recoat_sanded", "Recoat, sanded", "打磨重涂", [], []),
        ("recoat_unsanded", "Recoat, unsanded", "未打磨重涂", [], []),
        ("low_bake_refinish", "Low-bake refinish", "低温修补", [], []),
        ("spot_polish", "Spot polish", "局部抛光", [], []),
        ("pipe_loop_circulation", "Pipe-loop circulation", "管路循环", [], []),
        ("trim_tapes_adhesives", "Trim tapes and adhesives", "饰件胶带与胶粘剂", [], []),
        ("transport_protection_film", "Transport protection film", "运输保护膜", [], []),
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
        version=2,
        synthetic=True,
        source_note=(
            "Manually curated pilot metadata for the supplied MS-PNT-410 sample, not an "
            "authoritative standards database. Chinese aliases are demonstration terminology. "
            "Editions are unknown; method associations do not imply equivalence. "
            "Customer procedures are labeled separately from formal standard identifiers. "
            "Fluid names are not global test aliases; family mapping also requires the "
            "source chemical/fluid section and its stated procedure or conditions. "
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
                structured_limits=[
                    LabOperatingLimit(parameter="spacing", values=[Decimal("2")], unit="mm")
                ],
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
                structured_limits=[
                    LabOperatingLimit(
                        parameter="angle",
                        values=[Decimal("20"), Decimal("60"), Decimal("85")],
                        unit="°",
                    )
                ],
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
                structured_limits=[
                    LabOperatingLimit(parameter="load", values=[Decimal("750")], unit="g")
                ],
                notes="Synthetic declaration; price not supplied.",
            ),
            LabCapability(
                id="cap_stone_chip",
                canonical_test_id="stone_chip_resistance",
                method_ids=["iso_20567_1"],
                declared_constraints="23 °C only. Low-temperature testing is not declared.",
                structured_limits=[
                    LabOperatingLimit(parameter="temperature", values=[Decimal("23")], unit="°C")
                ],
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
