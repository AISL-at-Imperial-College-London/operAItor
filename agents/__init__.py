"""Monitoring (PCA + rules) → KG → planning → action agents, plus the operator chat agent.

Modules (import them directly; nothing heavy is imported at package level, so
``agents.kg`` works without langgraph installed):

    agents.monitoring      MonitoringAgent: concurrent detectors, diagnosis, debounce/cooldown
    agents.kg              KnowledgeGraph: diagnosis-seeded retrieval and free-text search
    agents.planning        KGPlanner / LLMPlanner: hypotheses → objective, levers, constraints
    agents.action          LLMActionAgent: brief → validated command plan
    agents.recovery_graph  the LangGraph (monitor → KG → plan → act → validate → twin → dispatch)
    agents.supervisor      AsyncSupervisor: runs the graph next to the live plant service
    agents.chat            ChatAgent: LangGraph tool loop over live data + KG (offline fallback)
"""
