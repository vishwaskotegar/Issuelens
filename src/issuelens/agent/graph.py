"""LangGraph state machine graph compilation and workflow assembly for IssueLens."""

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from issuelens.agent.nodes import InvestigationNodes, decide_next_step
from issuelens.agent.state import InvestigationState
from issuelens.core.logging import get_logger

logger = get_logger("issuelens.agent.graph")


def create_investigation_graph(
    nodes: InvestigationNodes,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build and compile the multi-round LangGraph investigation state machine.

    Workflow topology:
      START
        │
        ▼
      parse_issue
        │
        ▼
      retrieve_initial
        │
        ▼
      form_hypotheses
        │
        ▼
      evaluate_evidence ◄────────┐
        │                        │
        ▼                        │
      decide_next_step           │
        ├── (queries remain &    │
        │    round < max_rounds) │
        │    ▼                   │
        │   retrieve_followup ───┘
        │
        └── (stop condition or loop cap reached)
             ▼
            validate_citations
             │
             ▼
            build_report
             │
             ▼
            END
    """
    workflow: StateGraph[InvestigationState] = StateGraph(InvestigationState)

    # 1. Register discrete workflow nodes
    workflow.add_node("parse_issue", nodes.parse_issue)
    workflow.add_node("retrieve_initial", nodes.retrieve_initial)
    workflow.add_node("form_hypotheses", nodes.form_hypotheses)
    workflow.add_node("evaluate_evidence", nodes.evaluate_evidence)
    workflow.add_node("retrieve_followup", nodes.retrieve_followup)
    workflow.add_node("validate_citations", nodes.validate_citations)
    workflow.add_node("build_report", nodes.build_report)

    # 2. Sequential ingestion and hypothesis formation path
    workflow.add_edge(START, "parse_issue")
    workflow.add_edge("parse_issue", "retrieve_initial")
    workflow.add_edge("retrieve_initial", "form_hypotheses")
    workflow.add_edge("form_hypotheses", "evaluate_evidence")

    # 3. Conditional routing from evidence evaluation
    workflow.add_conditional_edges(
        "evaluate_evidence",
        decide_next_step,
        {
            "retrieve_followup": "retrieve_followup",
            "validate_citations": "validate_citations",
        },
    )

    # 4. Bounded loop back to evaluate_evidence
    workflow.add_edge("retrieve_followup", "evaluate_evidence")

    # 5. Citation validation and report synthesis terminal path
    workflow.add_edge("validate_citations", "build_report")
    workflow.add_edge("build_report", END)

    compiled = workflow.compile()
    logger.info("Compiled LangGraph investigation state machine successfully.")
    return compiled
