# UpgradePilot UI Design

## Design Direction

UpgradePilot is a professional developer tool for dependency upgrade
risk analysis and migration planning, not a chatbot.

The interface should feel like a modern developer platform:
information-dense, technical, trustworthy, and evidence-driven.

The UI should communicate three things clearly:

1. What the user is configuring.
2. What the agent is currently doing.
3. Why the agent reached its conclusions.

Do not optimize the interface for conversational chat.

---

## Canonical Visual References

The canonical UI screenshots are located in:

`docs/ui/screenshots/`

### 01 — New Migration Run

`01_new_migration_run_input.png`

Used as the visual reference for:

- Repository selection
- Dependency selection
- Version configuration
- Migration constraints
- Model configuration
- Knowledge base status
- Integration status
- Starting a migration audit

### 02 — Agent Activity

`02_agent_activity_running.png`

Used as the visual reference for:

- Active migration analysis
- Workflow progress
- Streaming agent activity
- LangGraph execution state
- Token and cost metrics
- RAG activity

### 03 — Human-in-the-Loop

`03_human_in_the_loop_interrupted.png`

Used as the visual reference for:

- LangGraph interruption
- Human decision interface
- Migration strategy choices
- Decision submission
- Resume workflow

The HITL state must be visually prominent and clearly communicate
that the agent is waiting for the user.

### 04 — Migration Report

`04_migration_report_overview.png`

Used as the visual reference for:

- Risk summary
- Migration complexity
- Impact breakdown
- Executive summary
- Recommended strategy
- Key breaking changes

### 05 — Code Changes

`05_code_changes_diff_view.png`

Used as the visual reference for:

- File changes
- Code diffs
- AI-generated change explanations
- Evidence associated with changes

### 06 — Pull Request Draft

`06_pull_request_draft_preview.png`

Used as the visual reference for:

- Pull request title
- PR description
- Branch information
- Change summary
- Risk summary
- Files affected
- Creating the PR

---

## Layout

UpgradePilot uses a three-region developer-console layout.

### 1. Left Sidebar

Contains persistent workspace navigation and configuration:

- New migration run
- Historical migration runs
- Model configuration
- Knowledge base status
- API/integration status
- Settings

The sidebar should remain visually stable while the main workspace changes.

### 2. Main Workspace

Contains the active migration workflow:

- Migration header
- Workflow progress
- Input configuration
- Agent activity
- Human-in-the-loop decision
- Migration report

The main workspace is the primary area of the application.

### 3. Right Telemetry Sidebar

Contains execution observability:

- Token usage
- Estimated cost
- LLM calls
- LangGraph execution state
- RAG sources
- Diagnostics
- Latency information

Telemetry should remain visible during an active migration run.

---

## Workflow States

The main workspace is state-driven.

### NEW

Display:

`InputView`

The user configures the repository, dependency, target version,
and migration constraints.

### RUNNING

Display:

`ActivityView`

The user sees:

- Workflow progress
- Streaming agent activity
- Current LangGraph node state
- RAG activity
- Token usage
- Cost

### INTERRUPTED

Display:

`DecisionView`

The user must explicitly choose a migration strategy before
the graph continues.

The interface must clearly communicate:

> The agent is waiting for your decision.

The user must not be able to accidentally continue without
submitting a decision.

### COMPLETED

Display:

`ReportView`

The user receives the final migration analysis.

The report can be inspected through:

- Overview
- Evidence
- Changes
- PR Draft

---

## Workflow Progress

The migration workflow should be visually represented as:

Repository Analysis
→ Dependency Analysis
→ Agentic RAG
→ Risk Assessment
→ Human Review
→ Migration Plan
→ Validation

Each step should communicate one of:

- Pending
- Running
- Completed
- Waiting for user
- Failed

The UI represents user-facing workflow states rather than individual
LangGraph implementation details.

---

## Visual Language

- Dark developer-tool interface
- Dense but readable information layout
- Subtle borders
- Rounded cards
- Strong information hierarchy
- Minimal decorative elements
- Semantic status colors
- Monospace typography where technical information benefits from it
- Consistent spacing and alignment
- High contrast for important warnings and decisions

The design should feel closer to a professional engineering platform
than a consumer AI application.

---

## Semantic Status Colors

Use color consistently for meaning.

### Success

Used for:

- Completed workflow steps
- Successful integrations
- Validated results
- Low-risk findings

### Warning

Used for:

- Human decisions
- Medium-risk findings
- Pending actions
- Attention required

### Error / High Risk

Used for:

- Failed workflow steps
- High-risk findings
- Blocking issues
- Validation failures

### Neutral

Used for:

- Pending steps
- Metadata
- Secondary information
- Technical diagnostics

Do not use status colors decoratively.

---

## Risk Presentation

Risk must never be presented as an unsupported LLM opinion.

The UI should distinguish:

- Risk level
- Confidence
- Migration complexity
- Affected files
- Breaking changes
- Supporting evidence

Example:

HIGH RISK
Confidence: 88%
Migration Complexity: 7.5 / 10

Every material risk finding should allow the user to inspect
the evidence supporting it.

---

## Evidence Presentation

RAG evidence should be inspectable.

A source should communicate:

- Document title
- Source type
- Relevance
- Relevant excerpt or finding
- Relationship to the current risk assessment

Evidence is supporting information, not a replacement for
repository analysis.

The UI should never imply that a retrieved document is relevant
merely because it was returned by vector search.

---

## Agent Activity

Agent activity should show observable execution events such as:

- Repository analysis started
- Dependency detected
- RAG search performed
- Sources retrieved
- Risk assessment generated
- Human review requested
- Decision received
- Migration plan generated
- Validation completed

Do not expose:

- Internal prompts
- Hidden chain-of-thought
- Private reasoning
- Secrets
- Credentials
- Sensitive repository contents unnecessarily

Technical details may be available through expandable diagnostics.

---

## Human-in-the-Loop

The HITL state is one of the primary interactions of the application.

When the LangGraph invokes `interrupt()`:

1. The workflow progress shows the Human Review step as active.
2. The main workspace displays the decision interface.
3. The user sees the relevant evidence and trade-offs.
4. The user explicitly selects an option.
5. The frontend submits the decision with the migration thread ID.
6. The graph resumes.
7. The UI returns to the running state.

The HITL component must never simulate an interruption purely
on the frontend.

The interruption state must originate from the backend workflow.

---

## Telemetry

Token and cost information is a first-class part of the interface.

During execution show:

- Input tokens
- Output tokens
- Total tokens
- Estimated cost
- LLM call count
- Elapsed time

The UI should update these values as execution progresses.

Telemetry should be informative without overwhelming the primary
migration workflow.

Detailed latency information belongs under diagnostics.

---

## Report Structure

The completed migration report contains:

### Overview

- Overall risk
- Confidence
- Migration complexity
- Impact breakdown
- Executive summary
- Recommended strategy
- Key breaking changes

### Evidence

- Repository evidence
- Migration documentation
- ADRs
- Retrieved RAG sources
- Evidence supporting individual findings

### Changes

- Affected files
- Proposed code changes
- Code diff
- Explanation of changes

### PR Draft

- Pull request title
- Description
- Branch information
- Change summary
- Risk summary
- Files affected

---

## Responsive Behavior

Desktop-first.

The three-region layout is optimized for desktop screens.

On smaller screens:

- Right telemetry sidebar becomes a drawer.
- Left navigation becomes collapsible.
- Main workspace remains primary.
- HITL decision cards remain fully usable.
- Tables and diffs may scroll horizontally where necessary.

Do not sacrifice critical workflow information merely to fit
everything into a narrow viewport.

---

## UX Principles

### Evidence over prose

The product should prefer verifiable evidence over plausible
AI-generated explanations.

### Transparency over complexity

Show what the agent is doing without exposing private reasoning.

### Human control

The agent can recommend actions, but explicit human decisions
control interruption points.

### Progressive disclosure

Show the most important information first. Detailed technical
information should be available without overwhelming the primary
workflow.

### Developer-first

The application should feel like an engineering tool rather than
an AI chatbot.

---

## Component Architecture

The component hierarchy is defined separately in:

`docs/ui/COMPONENTS.md`

`COMPONENTS.md` defines the React component structure.

This document defines visual design, behavior, workflow states,
and interaction principles.

The two documents should remain consistent.