"""Streamlit view for the document Research Copilot."""

from __future__ import annotations

import streamlit as st

from core.compliance.disclaimer import disclaimer_markdown
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
