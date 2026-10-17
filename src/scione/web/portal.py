"""Lab-facing prototype pages, separate from the extraction experiments."""

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
)

WORKSPACE_PAGES = ["My Lab", "Test Catalog", "Specifications", "Reports", "Developer Tools"]
NEW_CAPABILITY = "__new__"


def _method_label(catalog: ReferenceCatalog, method_id: str) -> str:
    method = catalog.method_by_id(method_id)
    return method.identifier + (f" · {method.edition}" if method.edition else "")


def _capability_label(catalog: ReferenceCatalog, capability: LabCapability) -> str:
    name = catalog.test_by_id(capability.canonical_test_id).name
    methods = ", ".join(_method_label(catalog, item) for item in capability.method_ids)
    return f"{name} · {methods or 'method not specified'} · {capability.id[-6:]}"


def _price_label(capability: LabCapability) -> str:
    if capability.price is None:
        return "Not supplied"
    return f"{capability.currency} {capability.price:f} / {capability.pricing_basis}"


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
    st.session_state["_capability_open_after_save"] = capability.id
    st.session_state["_workspace_notice"] = "Capability saved locally. No model request was made."
    st.rerun()


def _render_capability_editor(
    store: FileLabStore, lab: LabProfile, catalog: ReferenceCatalog
) -> None:
    st.subheader("Add or edit a capability")
    by_id = {item.id: item for item in lab.capabilities}
    pending = st.session_state.pop("_capability_open_after_save", None)
    if pending in by_id:
        st.session_state["capability_editor_choice"] = pending
    choice = st.selectbox(
        "Capability to edit",
        [NEW_CAPABILITY, *by_id],
        key="capability_editor_choice",
        format_func=lambda value: (
            "Add new capability"
            if value == NEW_CAPABILITY
            else _capability_label(catalog, by_id[value])
        ),
    )
    _initial_editor_values(lab, catalog, choice)
    if st.session_state["_capability_editor_version"] != lab.version:
        st.warning("The saved lab changed after this draft loaded. Reload before saving.")
        if st.button("Reload capability editor (discard draft)"):
            st.session_state.pop("_capability_editor_signature", None)
            _initial_editor_values(lab, catalog, choice)
    test_id = st.selectbox(
        "Test from the shared catalog",
        [item.id for item in catalog.tests],
        key="capability_test_id",
        format_func=lambda value: catalog.test_by_id(value).name,
    )
    test = catalog.test_by_id(test_id)
    if st.session_state.get("_capability_editor_test") != test_id:
        st.session_state["capability_method_ids"] = test.method_ids[:1]
        st.session_state["_capability_editor_test"] = test_id
    st.caption(test.description)
    method_ids = st.multiselect(
        "Methods this lab declares it can perform",
        test.method_ids,
        key="capability_method_ids",
        format_func=lambda value: _method_label(catalog, value),
        help="Listed methods are choices, not interchangeable procedures or verified capabilities.",
    )
    if not method_ids:
        st.info("No method declared. Future spec matching will need method review.")
    st.text_area(
        "Declared operating limits / conditions",
        key="capability_constraints",
        placeholder="For example: 23 °C only; minimum panel size; instrument limits.",
    )
    st.text_area("Notes", key="capability_notes")
    has_price = st.checkbox("Record an optional demo price", key="capability_has_price")
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
        st.caption("Demo metadata only. A stored price is not a complete customer quotation.")
    st.session_state["_capability_pricing_visible"] = has_price
    if st.button("Save capability", type="primary"):
        _save_capability(store, lab, choice, test_id, method_ids)


def render_my_lab(store: FileLabStore, catalog: ReferenceCatalog) -> None:
    st.title("My Lab")
    st.caption("A local demo workspace, not a login or multi-user account system.")
    st.warning(
        "The initial lab, its capabilities and prices are synthetic demo data. "
        "Entries are lab declarations, not independently verified support or accreditation."
    )
    lab = store.load_or_create()
    notice = st.session_state.pop("_workspace_notice", None)
    if notice:
        st.success(notice)
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
        st.subheader("Lab details")
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

    # Show the current list after a save, rather than a stale pre-submit table.
    _render_capability_editor(store, lab, catalog)
    current = store.load_or_create()
    st.subheader("Saved capabilities")
    st.caption(
        f"{len(current.capabilities)} entries · local revision {current.version}. "
        "Switch pages or reopen the app to verify persistence."
    )
    rows = [
        {
            "Test": catalog.test_by_id(item.canonical_test_id).name,
            "Declared methods": ", ".join(_method_label(catalog, key) for key in item.method_ids)
            or "Not specified",
            "Declared conditions": item.declared_constraints or "Not specified",
            "Price": _price_label(item),
            "Notes": item.notes or "",
        }
        for item in current.capabilities
    ]
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.info("No capabilities saved yet. Add a test using the form above.")
    st.caption("Data is saved to the local workspace, not a database or cloud service.")


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


def render_pending_page(page: str) -> None:
    st.title(page)
    if page == "Specifications":
        st.info(
            "Not implemented yet: customer-spec upload, saved documents and "
            "side-by-side PDF / requirement review are the next implementation step."
        )
        st.caption("Use Developer Tools for the existing legacy PDF extraction experiments.")
    else:
        st.info(
            "Not implemented yet: reviewed requirements will be matched against this lab's "
            "declared capabilities to generate a PDF gap report and JSON "
            "from the same saved results."
        )
        st.caption("No report, model accuracy or capability-match result is being simulated here.")
