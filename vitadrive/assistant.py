""""Ask VitaDrive": answers questions about one car with LangChain, using the car's health report as context.

Turned on by setting ``OPENAI_API_KEY`` (and optionally ``OPENAI_MODEL``). The model only ever sees the report
of the vehicle being asked about, which the API has already checked belongs to the signed-in user.
"""
from __future__ import annotations

import os

from .service import HealthReport

DEFAULT_MODEL = "gpt-4o-mini"
SYSTEM_PROMPT = (
    "You are VitaDrive, a friendly car health assistant. Answer the driver's question using only the vehicle "
    "data provided: live readings, active alerts, the maintenance forecast, estimated range and the learned "
    "fault detector's result. Lead with what needs attention most and give concrete dates or distances when "
    "the data has them. Keep answers short and plain. If the data doesn't answer the question, say so. "
    "For anything safety-critical (brakes, overheating, low oil pressure) advise seeing a mechanic."
)


def enabled() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def default_llm():
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL, temperature=0.2, timeout=30,
                      max_retries=1)


def context(report: HealthReport) -> str:
    return report.model_dump_json(exclude_none=True)


def ask(report: HealthReport, question: str, llm=None) -> str:
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", "Vehicle data (JSON):\n{context}\n\nQuestion: {question}"),
    ])
    chain = prompt | (llm or default_llm()) | StrOutputParser()
    return chain.invoke({"context": context(report), "question": question}).strip()
