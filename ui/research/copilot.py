"""Streamlit view for the document Research Copilot."""

from __future__ import annotations

import streamlit as st

from core.compliance.disclaimer import disclaimer_markdown
from modules.research.facts_store import FactCandidate, FactsStore, extract_fact_candidates
from modules.research.thesis_reality import ThesisClaim, evaluate_thesis_claim
from modules.research.governance_flags import extract_governance_candidates
from modules.research.service import (
    analyze_document,
    analyze_with_ai,
    answer_question,
    answer_question_with_ai,
    extract_document,
)


def render() -> None:
    st.subheader("AI Research Copilot")
    st.caption("Annual report, concall transcript, ya investor presentation upload karke cited research evidence dekhein.")
    st.info("Only uploaded-document evidence is used. Unsupported facts are not invented.")
    st.caption("Educational tool. Not investment advice.")
    uploaded = st.file_uploader("Upload a PDF", type=["pdf"], key="research_copilot_pdf")
    if uploaded is None:
        st.markdown(disclaimer_markdown())
        return
    try:
        document = extract_document(uploaded, uploaded.name)
    except (ValueError, RuntimeError) as exc:
        st.error(str(exc))
        st.markdown(disclaimer_markdown())
        return
    st.success(f"Read {len(document.pages)} pages from {document.name}.")

    with st.expander("Facts Store · Extract reviewable fact candidates", expanded=True):
        st.caption(
            "Yahan ke extracted numbers candidate hain, verified facts nahi. "
            "Har candidate ke saath source page aur original passage save hota hai. "
            "Standalone/consolidated basis aur units ko verify karna zaroori hai."
        )
        company_name = st.text_input(
            "Company name", key="research_facts_company",
            placeholder="e.g. Example Industries Ltd.",
        )
        if st.button("Extract fact candidates", key="research_extract_facts"):
            try:
                candidates = extract_fact_candidates(document, company_name)
                st.session_state["research_fact_candidates"] = [item.as_dict() for item in candidates]
                st.session_state["research_fact_candidates_document"] = document.name
            except ValueError as exc:
                st.error(str(exc))
        candidates = st.session_state.get("research_fact_candidates", [])
        if candidates and st.session_state.get("research_fact_candidates_document") == document.name:
            st.write(f"Candidates found: {len(candidates)} · Status: needs_review")
            st.dataframe(
                [{
                    "Metric": item["metric_name"],
                    "Reported value": item["reported_value"],
                    "Unit": item["unit"] or "Not specified",
                    "Period": item["period"] or "Not found in documents",
                    "PDF page": item["page_number"],
                    "Verification": item["verification_status"],
                    "Source passage": item["supporting_passage"],
                } for item in candidates],
                use_container_width=True,
                hide_index=True,
            )
            if st.button("Save candidates to local Facts Store", key="research_save_facts"):
                try:
                    inserted = FactsStore().save_candidates(
                        FactCandidate(**item) for item in candidates
                    )
                    st.success(f"Saved {inserted} new fact candidates. Duplicate facts are not inserted again.")
                except (OSError, RuntimeError, ValueError) as exc:
                    st.error(f"Facts Store save failed: {exc}")
        elif st.session_state.get("research_fact_candidates_document") == document.name:
            st.info("No matching financial fact candidates found. No facts were fabricated.")

    with st.expander("Thesis vs Reality · Human-verified facts only", expanded=False):
        st.caption(
            "Status deterministic rules se calculate hota hai, AI guess nahi karta. "
            "Sirf human-verified facts use honge; source document aur page result ke saath dikhenge."
        )
        thesis_company = st.text_input(
            "Company to evaluate", key="thesis_reality_company",
            value=st.session_state.get("research_facts_company", ""),
        ).strip()
        facts_store = FactsStore()
        company_facts = facts_store.list_facts(thesis_company) if thesis_company else []
        if not company_facts:
            st.info("Is company ke saved facts nahi mile. Pehle candidates save karein.")
        else:
            pending_facts = [item for item in company_facts if item["verification_status"] == "needs_review"]
            if pending_facts:
                st.markdown("#### 1. Review extracted candidates")
                pending_labels = {
                    f"{item['metric_name']} · {item.get('period') or 'period missing'} · {item['reported_value']} · page {item['page_number']} · {item['fact_id'][:8]}": item
                    for item in pending_facts
                }
                chosen_label = st.selectbox("Candidate to review", list(pending_labels), key="thesis_review_fact")
                chosen_fact = pending_labels[chosen_label]
                review_status = st.selectbox("Review decision", ["verified", "rejected", "needs_review"], key="thesis_review_status")
                normalized_value = st.text_input(
                    "Verified numeric value (same scale as the source)",
                    value=str(chosen_fact.get("normalized_value") or chosen_fact["reported_value"]),
                    key="thesis_review_normalized_value",
                )
                reporting_basis = st.selectbox(
                    "Reporting basis", ["", "consolidated", "standalone", "not_applicable"],
                    index=0, key="thesis_review_basis",
                )
                review_notes = st.text_input("Review note (optional)", key="thesis_review_notes")
                st.caption(f"Source: {chosen_fact['source_document_title']} · Page {chosen_fact['page_number']} — {chosen_fact['supporting_passage']}")
                if st.button("Save human review", key="thesis_save_review"):
                    if review_status == "verified" and (not normalized_value.strip() or not reporting_basis):
                        st.error("Verified facts need a numeric value and explicit reporting basis.")
                    else:
                        saved = facts_store.set_verification_status(
                            chosen_fact["fact_id"], review_status,
                            normalized_value=normalized_value.strip() or None,
                            reporting_basis=reporting_basis or None,
                            notes=review_notes.strip() or None,
                        )
                        if saved:
                            st.success("Review saved. Re-open this section or rerun the page to refresh the list.")
                        else:
                            st.error("Fact could not be updated.")
            verified_facts = [item for item in company_facts if item["verification_status"] == "verified"]
            available_metrics = sorted({item["metric_name"] for item in verified_facts})
            if available_metrics:
                st.markdown("#### 2. Compare a thesis with reported reality")
                with st.form("thesis_reality_form"):
                    thesis_text = st.text_input("Your thesis (plain language)", placeholder="Revenue should keep growing")
                    selected_metric = st.selectbox("Metric", available_metrics)
                    metric_facts = [item for item in verified_facts if item["metric_name"] == selected_metric]
                    periods = sorted({str(item["period"]) for item in metric_facts if item.get("period")})
                    if len(periods) < 2:
                        st.info("Is metric ke liye kam se kam do verified reporting periods chahiye.")
                        baseline_period = latest_period = periods[0] if periods else ""
                    else:
                        baseline_period = st.selectbox("Baseline period", periods, index=0)
                        latest_options = [period for period in periods if period != baseline_period]
                        latest_period = st.selectbox("Latest period", latest_options, index=len(latest_options) - 1)
                    direction = st.selectbox("Expected direction", ["increase", "decrease", "stable"])
                    tolerance = st.number_input("Tolerance (%)", min_value=0.0, max_value=100.0, value=5.0, step=1.0)
                    submitted = st.form_submit_button("Evaluate thesis")
                if submitted:
                    if len(periods) < 2 or not thesis_text.strip():
                        st.warning("Thesis text aur do distinct periods zaroori hain.")
                    else:
                        claim = ThesisClaim(
                            claim_id=f"{thesis_company}|{selected_metric}|{baseline_period}|{latest_period}",
                            company_name=thesis_company, metric_name=selected_metric,
                            thesis_text=thesis_text.strip(), expected_direction=direction,
                            baseline_period=baseline_period, latest_period=latest_period,
                            tolerance_pct=float(tolerance),
                        )
                        result = evaluate_thesis_claim(claim, company_facts)
                        st.session_state["thesis_reality_last_result"] = result
                result = st.session_state.get("thesis_reality_last_result")
                if result and result.get("company_name", "").casefold() == thesis_company.casefold():
                    st.markdown(f"### Status: {result['status']}")
                    st.write(result["thesis_text"])
                    if result["change_pct"] is not None:
                        st.metric("Reported change", f"{result['change_pct']:+.2f}%")
                    st.caption(result["explanation"])
                    for source in result["sources"]:
                        st.write(
                            f"Source: {source['document']} · Page {source['page']} · "
                            f"{source['period']} · {source['value']} {source['unit'] or ''} · "
                            f"basis: {source['reporting_basis'] or 'not specified'}"
                        )
                    st.caption("Educational tool. Not investment advice.")
            else:
                st.info("Thesis status tabhi available hoga jab at least one fact human-verified ho.")
    with st.expander("Governance Red-Flag Review · Evidence candidates", expanded=False):
        st.caption(
            "Yeh keyword-based review candidates hain, confirmed allegations nahi. "
            "Har signal ko source page par manually verify karein. Absence of a signal "
            "does not prove absence of governance risk."
        )
        try:
            governance_candidates = extract_governance_candidates(document)
            if governance_candidates:
                st.dataframe(
                    [{
                        "Category": item.category.replace("_", " ").title(),
                        "Signal": item.signal,
                        "Review status": item.review_status,
                        "Document": item.source_document,
                        "Page": item.page_number,
                        "Evidence passage": item.evidence,
                    } for item in governance_candidates],
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("No matching governance phrases found in readable text. This is not a clean-bill-of-health result.")
        except ValueError as exc:
            st.warning(str(exc))

    if st.button("Generate one-page AI research summary", type="primary", key="research_generate"):
        try:
            with st.spinner("Preparing cited analysis..."):
                analysis, router = analyze_with_ai(document)
            st.session_state["research_copilot_analysis"] = {
                "summary": analysis.summary,
                "key_risks": list(analysis.key_risks),
                "red_flags": list(analysis.red_flags),
            }
            if router.status.development_mode:
                st.warning(f"DEVELOPMENT MODE — {router.status.message}")
            else:
                st.caption(f"AI provider: {router.status.active.title()}")
            for title, value in (
                ("Summary", analysis.summary),
                ("Company kya karti hai?", analysis.business_model),
                ("Important financial trends", analysis.financial_trends),
                ("Management commentary", analysis.management_commentary),
                ("Uncertainty", analysis.uncertainty),
            ):
                st.markdown(f"### {title}")
                st.write(value)
            for title, values in (("Key risks", analysis.key_risks), ("Red flags", analysis.red_flags),
                                  ("What to investigate further", analysis.further_research)):
                st.markdown(f"### {title}")
                for value in values:
                    st.write(f"- {value}")
            with st.expander("Sources"):
                for citation in analysis.citations:
                    st.write(f"{citation.document} · Page {citation.page}")
                    st.caption(citation.evidence)
        except (ValueError, RuntimeError) as exc:
            st.error(f"Analysis unavailable: {exc}")
    for topic, evidence in analyze_document(document).items():
        with st.expander(topic, expanded=True):
            for item in evidence:
                st.write(item)
    question = st.text_input("Ask a question about this document", key="research_copilot_question")
    if question:
        st.markdown("**Answer**")
        try:
            answer, citations, router = answer_question_with_ai(document, question)
            if router.status.development_mode:
                st.warning("DEVELOPMENT MODE — AI provider unavailable")
            st.write(answer)
            for citation in citations:
                st.caption(f"Source: {citation.document} · Page {citation.page}")
        except (ValueError, RuntimeError):
            st.write(answer_question(document, question))
    st.markdown(disclaimer_markdown())
