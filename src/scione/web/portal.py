"""Streamlit lab profile, catalog and workspace navigation."""

from __future__ import annotations

from uuid import uuid4

import streamlit as st
from pydantic import ValidationError

from scione.catalogs import (
    FileLabStore,
    LabCapability,
    LabProfile,
    LabStoreError,
    ReferenceCatalog,
    default_demo_lab,
)
from scione.catalogs.models import LabOperatingLimit

WORKSPACE_PAGES = ["My Lab", "Test Catalog", "Specifications", "Reports", "Developer Tools"]
NEW_CAPABILITY = "__new__"
_OPERATING_PARAMETERS = {
    "temperature": ("Temperature", "°C"),
    "load": ("Applied load", "g"),
    "spacing": ("Cutter spacing", "mm"),
    "duration": ("Exposure / test duration", "h"),
    "angle": ("Measurement angle", "°"),
    "pressure": ("Pressure", "bar"),
    "cycles": ("Number of cycles", "cycles"),
}


def _select_workspace(page: str) -> None:
    st.session_state["workspace_page"] = page


def render_workspace_navigation() -> str:
    """Use real menu buttons instead of disguising a single-choice input as navigation."""

    if st.session_state.get("workspace_page") not in WORKSPACE_PAGES:
        st.session_state["workspace_page"] = WORKSPACE_PAGES[0]
    with st.sidebar.container(key="workspace_navigation"):
        for page in WORKSPACE_PAGES[:-1]:
            st.button(
                page,
                key="nav_" + page.lower().replace(" ", "_"),
                width="stretch",
                type="primary" if st.session_state["workspace_page"] == page else "secondary",
                on_click=_select_workspace,
                args=(page,),
            )
        with st.expander("Developer tools"):
            st.button(
                "Developer Tools",
                key="nav_developer_tools",
                width="stretch",
                type=(
                    "primary"
                    if st.session_state["workspace_page"] == "Developer Tools"
                    else "secondary"
                ),
                on_click=_select_workspace,
                args=("Developer Tools",),
            )
    return st.session_state["workspace_page"]


def _method_label(catalog: ReferenceCatalog, method_id: str) -> str:
    method = catalog.method_by_id(method_id)
    return method.identifier + (f" · {method.edition}" if method.edition else "")


def _capability_label(catalog: ReferenceCatalog, capability: LabCapability) -> str:
    name = catalog.test_by_id(capability.canonical_test_id).name
    methods = ", ".join(_method_label(catalog, item) for item in capability.method_ids)
    return f"{name} · {methods or 'method not specified'}"


def _price_label(capability: LabCapability) -> str:
    if capability.price is None:
        return "Not supplied"
    return f"{capability.currency} {capability.price:f} / {capability.pricing_basis}"


def _open_capability_editor(choice: str) -> None:
    st.session_state["capability_editor_choice"] = choice
    st.session_state["_capability_editor_open"] = True
    st.session_state.pop("_capability_editor_signature", None)


def _close_capability_editor() -> None:
    st.session_state["_capability_editor_open"] = False
    st.session_state.pop("_capability_editor_signature", None)


def _initial_editor_values(lab: LabProfile, catalog: ReferenceCatalog, choice: str) -> None:
    signature = (lab.id, choice)
    # Streamlit removes widget state when another workspace page is displayed.
    if (
        st.session_state.get("_capability_editor_signature") == signature
        and "capability_test_id" in st.session_state
    ):
        return
    capability = next((item for item in lab.capabilities if item.id == choice), None)
    test_id = capability.canonical_test_id if capability else catalog.tests[0].id
    test = catalog.test_by_id(test_id)
    st.session_state.update(
        {
            "_capability_editor_signature": signature,
            "_capability_editor_test": test_id,
            "_capability_editor_version": lab.version,
            "capability_test_id": test_id,
            "capability_method_ids": (
                list(capability.method_ids) if capability else test.method_ids[:1]
            ),
            "capability_constraints": capability.declared_constraints or "" if capability else "",
            "capability_notes": capability.notes or "" if capability else "",
            "capability_has_price": capability is not None and capability.price is not None,
            "_capability_pricing_visible": capability is not None and capability.price is not None,
            "capability_price": (
                format(capability.price, "f")
                if capability is not None and capability.price is not None
                else ""
            ),
            "capability_currency": capability.currency or "USD" if capability else "USD",
            "capability_pricing_basis": (
                capability.pricing_basis or "per specimen" if capability else "per specimen"
            ),
        }
    )
    st.session_state["_capability_pricing_draft"] = {
        key: st.session_state[key]
        for key in ("capability_price", "capability_currency", "capability_pricing_basis")
    }
    limits = {item.parameter: item for item in capability.structured_limits} if capability else {}
    # Keep the original declarations when the user only edits a price or a note.
    # In particular, never infer new operating limits from the old prose field.
    st.session_state["_capability_original_limits"] = (
        capability.structured_limits if capability else []
    )
    for parameter in _OPERATING_PARAMETERS:
        limit = limits.get(parameter)
        prefix = f"capability_limit_{parameter}"
        st.session_state[prefix + "_enabled"] = limit is not None
        st.session_state[prefix + "_minimum"] = (
            format(limit.minimum, "f") if limit is not None and limit.minimum is not None else ""
        )
        st.session_state[prefix + "_maximum"] = (
            format(limit.maximum, "f") if limit is not None and limit.maximum is not None else ""
        )
        st.session_state[prefix + "_values"] = (
            ", ".join(format(value, "f") for value in limit.values) if limit else ""
        )
    for parameter in ("condition", "scope", "instruction"):
        limit = limits.get(parameter)
        st.session_state[f"capability_limit_{parameter}"] = (
            "\n".join(limit.supported_values) if limit else ""
        )


def _structured_editor_limits() -> list[LabOperatingLimit]:
    limits = []
    for parameter, (_, unit) in _OPERATING_PARAMETERS.items():
        prefix = f"capability_limit_{parameter}"
        if not st.session_state[prefix + "_enabled"]:
            continue
        minimum = st.session_state[prefix + "_minimum"].strip()
        maximum = st.session_state[prefix + "_maximum"].strip()
        values = [
            value.strip()
            for value in st.session_state[prefix + "_values"].split(",")
            if value.strip()
        ]
        limits.append(
            LabOperatingLimit(
                parameter=parameter,
                minimum=minimum or None,
                maximum=maximum or None,
                unit=unit,
                values=values,
            )
        )
    for parameter in ("condition", "scope", "instruction"):
        values = [
            value.strip()
            for value in st.session_state[f"capability_limit_{parameter}"].splitlines()
            if value.strip()
        ]
        if values:
            limits.append(LabOperatingLimit(parameter=parameter, supported_values=values))
    # A legacy caller may have multiple same-parameter ranges. If the visible
    # values were untouched, preserve those ranges rather than collapsing them.
    original = st.session_state.get("_capability_original_limits", [])
    for parameter in {item.parameter for item in original}:
        prior = [item for item in original if item.parameter == parameter]
        current = [item for item in limits if item.parameter == parameter]
        if len(prior) > 1 and current == prior[-1:]:
            limits = [item for item in limits if item.parameter != parameter] + prior
    return limits


def _render_structured_limits() -> None:
    with st.expander("Structured operating limits (optional)"):
        st.caption(
            "Explicit lab inputs let the system compare conditions automatically. "
            "Use a range OR discrete values; use equal minimum and maximum for a fixed setting. "
            "Unfilled fields stay unknown. Existing notes and restrictions still apply."
        )
        for parameter, (label, unit) in _OPERATING_PARAMETERS.items():
            prefix = f"capability_limit_{parameter}"
            enabled = st.checkbox(f"Declare {label.lower()} ({unit})", key=prefix + "_enabled")
            columns = st.columns(3)
            with columns[0]:
                st.text_input(
                    f"Minimum {label.lower()}", key=prefix + "_minimum", disabled=not enabled
                )
            with columns[1]:
                st.text_input(
                    f"Maximum {label.lower()}", key=prefix + "_maximum", disabled=not enabled
                )
            with columns[2]:
                st.text_input(
                    f"Discrete {label.lower()} values",
                    key=prefix + "_values",
                    disabled=not enabled,
                    placeholder="For example: 20, 60, 85"
                    if parameter == "angle"
                    else "Comma-separated",
                )
        for parameter, label in (
            ("condition", "Other supported operating clauses"),
            ("scope", "Supported customer scope"),
            ("instruction", "Supported special instructions"),
        ):
            st.text_area(
                label,
                key=f"capability_limit_{parameter}",
                height=80,
                help=(
                    "One precise phrase per line. "
                    "These require an exact text match, not an AI guess."
                ),
            )
        original = st.session_state.get("_capability_original_limits", [])
        if len(original) != len({item.parameter for item in original}):
            st.caption(
                "This entry has multiple ranges for one setting; "
                "unchanged ranges are preserved on save."
            )


def _save_capability(
    store: FileLabStore, lab: LabProfile, choice: str, test_id: str, method_ids: list[str]
) -> None:
    try:
        has_price = st.session_state["capability_has_price"]
        if has_price and not st.session_state["capability_price"].strip():
            st.error("Enter a price, or turn off optional pricing.")
            return
        capability = LabCapability(
            id=f"cap_{uuid4().hex}" if choice == NEW_CAPABILITY else choice,
            canonical_test_id=test_id,
            method_ids=method_ids,
            declared_constraints=st.session_state["capability_constraints"].strip() or None,
            structured_limits=_structured_editor_limits(),
            notes=st.session_state["capability_notes"].strip() or None,
            price=st.session_state["capability_price"].strip() if has_price else None,
            currency=st.session_state["capability_currency"].strip().upper() if has_price else None,
            pricing_basis=(
                st.session_state["capability_pricing_basis"].strip() if has_price else None
            ),
        )
        capabilities = list(lab.capabilities)
        if choice == NEW_CAPABILITY:
            capabilities.append(capability)
        else:
            capabilities = [capability if item.id == choice else item for item in capabilities]
        updated = LabProfile.model_validate(
            {
                **lab.model_dump(),
                "version": st.session_state["_capability_editor_version"],
                "capabilities": capabilities,
            }
        )
        store.save(updated)
    except (ValidationError, LabStoreError) as exc:
        st.error(f"Capability was not saved: {exc}")
        return
    if st.session_state.get("_lab_details_version") == lab.version:
        st.session_state["_lab_details_version"] = lab.version + 1
    st.session_state.pop("_capability_editor_signature", None)
    st.session_state["_capability_editor_open"] = False
    st.session_state["_workspace_notice"] = "Test saved. Your lab's list is up to date."
    st.rerun()


def _render_capability_editor(
    store: FileLabStore, lab: LabProfile, catalog: ReferenceCatalog
) -> None:
    choice = st.session_state["capability_editor_choice"]
    if choice != NEW_CAPABILITY and not any(item.id == choice for item in lab.capabilities):
        st.warning("This test is no longer in your lab's list. Choose another test to edit.")
        _close_capability_editor()
        return
    st.subheader("Add a test" if choice == NEW_CAPABILITY else "Edit test")
    _initial_editor_values(lab, catalog, choice)
    if st.session_state["_capability_editor_version"] != lab.version:
        st.warning("The saved lab changed after this draft loaded. Reload before saving.")
        if st.button("Reload capability editor (discard draft)"):
            st.session_state.pop("_capability_editor_signature", None)
            _initial_editor_values(lab, catalog, choice)
    test_id = st.selectbox(
        "Test",
        [item.id for item in catalog.tests],
        key="capability_test_id",
        format_func=lambda value: catalog.test_by_id(value).name,
    )
    test = catalog.test_by_id(test_id)
    if st.session_state.get("_capability_editor_test") != test_id:
        st.session_state["capability_method_ids"] = test.method_ids[:1]
        st.session_state["_capability_editor_test"] = test_id
    method_ids = st.multiselect(
        "Methods your lab offers",
        test.method_ids,
        key="capability_method_ids",
        format_func=lambda value: _method_label(catalog, value),
        help=(
            "Choose the procedures your lab uses. "
            "Different methods are not automatically equivalent."
        ),
    )
    if not method_ids:
        st.caption("No method selected. You can add one later; matching will need a review.")
    st.text_area(
        "Operating conditions or limits",
        key="capability_constraints",
        placeholder="For example: 23 °C only; minimum panel size; instrument limits.",
    )
    _render_structured_limits()
    with st.expander("Additional notes"):
        st.text_area("Notes", key="capability_notes")
    has_price = st.checkbox("Include a price", key="capability_has_price")
    if has_price:
        if not st.session_state.get("_capability_pricing_visible", False):
            for key, value in st.session_state["_capability_pricing_draft"].items():
                st.session_state[key] = value
        price_column, currency_column, basis_column = st.columns([1, 1, 2])
        with price_column:
            st.text_input("Price", key="capability_price", placeholder="125.50")
        with currency_column:
            st.text_input("Currency", key="capability_currency", max_chars=3)
        with basis_column:
            st.text_input("Pricing basis", key="capability_pricing_basis")
        st.session_state["_capability_pricing_draft"] = {
            key: st.session_state[key]
            for key in ("capability_price", "capability_currency", "capability_pricing_basis")
        }
        st.caption("Reference price only, not a customer quotation.")
    st.session_state["_capability_pricing_visible"] = has_price
    save_column, cancel_column = st.columns([1, 1])
    with save_column:
        if st.button("Save test", key="save_capability", type="primary", width="stretch"):
            _save_capability(store, lab, choice, test_id, method_ids)
    with cancel_column:
        st.button("Cancel", key="cancel_capability", on_click=_close_capability_editor)


def _render_lab_details(store: FileLabStore, lab: LabProfile) -> LabProfile:
    if "lab_name" not in st.session_state:
        st.session_state["lab_name"] = lab.name
        st.session_state["lab_description"] = lab.description
        st.session_state["_lab_details_version"] = lab.version
    if st.session_state.get("_lab_details_version") != lab.version:
        st.warning("Lab details changed since this form loaded. Reload before saving.")
        if st.button("Reload lab details (discard edits)"):
            st.session_state["lab_name"] = lab.name
            st.session_state["lab_description"] = lab.description
            st.session_state["_lab_details_version"] = lab.version
    with st.form("lab_details"):
        name = st.text_input("Lab name", key="lab_name")
        description = st.text_area("About this lab", key="lab_description")
        if st.form_submit_button("Save lab details"):
            try:
                updated = LabProfile.model_validate(
                    {
                        **lab.model_dump(),
                        "version": st.session_state["_lab_details_version"],
                        "name": name,
                        "description": description,
                    }
                )
                store.save(updated)
                previous_version = lab.version
                lab = store.load_or_create()
                st.session_state["_lab_details_version"] = lab.version
                if st.session_state.get("_capability_editor_version") == previous_version:
                    st.session_state["_capability_editor_version"] = lab.version
                st.success("Lab details saved locally.")
            except (ValidationError, LabStoreError) as exc:
                st.error(f"Lab details were not saved: {exc}")

    return lab


def render_my_lab(store: FileLabStore, catalog: ReferenceCatalog) -> None:
    st.title("My Lab")
    st.caption("Manage the tests, methods and reference prices your lab offers.")
    lab = store.load_or_create()
    notice = st.session_state.pop("_workspace_notice", None)
    if notice:
        st.success(notice)
    st.subheader(lab.name)
    st.caption(
        "Demo workspace · initial entries and prices are synthetic. "
        "Lab declarations are not independently verified."
    )
    with st.expander("Lab details"):
        lab = _render_lab_details(store, lab)

    heading_column, add_column = st.columns([3, 1])
    with heading_column:
        st.subheader("Tests your lab offers")
        st.caption(f"{len(lab.capabilities)} saved tests")
    with add_column:
        st.button(
            "Add a test",
            key="add_capability",
            type="primary",
            width="stretch",
            on_click=_open_capability_editor,
            args=(NEW_CAPABILITY,),
        )
    rows = [
        {
            "Test": catalog.test_by_id(item.canonical_test_id).name,
            "Methods": ", ".join(_method_label(catalog, key) for key in item.method_ids)
            or "Not specified",
            "Conditions": item.declared_constraints or "Not specified",
            "Price": _price_label(item),
        }
        for item in lab.capabilities
    ]
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.info("Your list is empty. Choose Add a test to record your first capability.")
        st.caption("For a demonstration, you can explicitly load invented examples instead.")
        if st.button("Load example lab", key="load_example_lab"):
            example = default_demo_lab()
            try:
                store.save(lab.model_copy(update={"capabilities": example.capabilities}))
                if st.session_state.get("_lab_details_version") == lab.version:
                    st.session_state["_lab_details_version"] = lab.version + 1
                st.session_state.pop("_capability_editor_signature", None)
                st.session_state["_capability_editor_open"] = False
                st.session_state["_workspace_notice"] = (
                    "Synthetic example capabilities loaded. Review or edit them before using them."
                )
                st.rerun()
            except (ValidationError, LabStoreError) as exc:
                st.error(f"Example lab was not loaded: {exc}")
    if lab.capabilities:
        by_id = {item.id: item for item in lab.capabilities}
        with st.expander("Edit a saved test"):
            choice = st.selectbox(
                "Choose a test",
                list(by_id),
                key="capability_to_edit",
                format_func=lambda value: _capability_label(catalog, by_id[value]),
            )
            st.button(
                "Edit test",
                key="edit_capability",
                on_click=_open_capability_editor,
                args=(choice,),
            )
    if st.session_state.get("_capability_editor_open", False):
        with st.container(border=True):
            _render_capability_editor(store, lab, catalog)


def render_test_catalog(catalog: ReferenceCatalog) -> None:
    st.title("Test Catalog")
    st.caption(catalog.source_note)
    st.info(
        "Shared test/method identities and curated demo aliases only, not standard documents. "
        "Associated methods are not automatically equivalent."
    )
    query = (
        st.text_input("Search tests, aliases or method codes", key="catalog_search")
        .strip()
        .casefold()
    )
    rows = []
    for test in catalog.tests:
        methods = ", ".join(_method_label(catalog, key) for key in test.method_ids)
        aliases = "; ".join(f"{item.text} ({item.language})" for item in test.aliases)
        searchable = " ".join([test.id, test.name, test.description, aliases, methods]).casefold()
        if query and query not in searchable:
            continue
        rows.append(
            {
                "Test": test.name,
                "Test ID": test.id,
                "Aliases": aliases,
                "Associated methods": methods,
            }
        )
    st.caption(f"{len(rows)} of {len(catalog.tests)} pilot test identities")
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.info("No catalog entries found. This pilot catalog is intentionally incomplete.")
    with st.expander("Method identities and editions"):
        st.dataframe(
            [
                {
                    "Method ID": method.id,
                    "Method": method.identifier,
                    "Edition": method.edition or "Not specified",
                }
                for method in catalog.methods
            ],
            width="stretch",
            hide_index=True,
        )
