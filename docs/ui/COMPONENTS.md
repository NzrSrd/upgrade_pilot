# UpgradePilot UI Component Architecture

The frontend follows a three-region developer-console layout:

AppShell
│
├── AppHeader
│   ├── ProductLogo
│   ├── MigrationRunInfo
│   ├── TargetStack
│   └── RunStatus
│
├── LeftSidebar
│   ├── NewMigrationButton
│   ├── RunHistory
│   │   └── RunHistoryItem
│   ├── ConfigurationPanel
│   │   ├── ModelSelector
│   │   ├── TemperatureControl
│   │   ├── KnowledgeBaseStatus
│   │   └── IntegrationsStatus
│   └── Settings
│
├── MainWorkspace
│   ├── MigrationHeader
│   ├── WorkflowProgress
│   │   └── WorkflowStep
│   │
│   └── WorkspaceTabs
│       ├── InputView
│       ├── ActivityView
│       ├── DecisionView
│       └── ReportView
│           └── MigrationReport
│               ├── ReportOverview
│               ├── EvidenceView
│               ├── ChangesView
│               └── PullRequestView
│
└── TelemetrySidebar
    ├── UsageMetrics
    ├── GraphExecutionState
    │   └── GraphNodeStatus
    ├── RagSources
    │   └── EvidenceSource
    └── Diagnostics

The UI is state-driven rather than node-driven. React components
represent user-facing workflow states and information, not individual
LangGraph implementation nodes.

The canonical visual references are located in:

docs/ui/screenshots/

- 01_new_migration_run_input.png
- 02_agent_activity_running.png
- 03_human_in_the_loop_interrupted.png
- 04_migration_report_overview.png
- 05_code_changes_diff_view.png
- 06_pull_request_draft_preview.png

## Workspace State

The `WorkspaceTabs` displays the appropriate workflow view based on
the current migration run state. Users may manually navigate between
available views when the state permits it.

NEW
 ↓
InputView

RUNNING
 ↓
ActivityView

INTERRUPTED
 ↓
DecisionView

COMPLETED
 ↓
ReportView

## Workflow Model

                    MainWorkspace
                         │
                 WorkflowProgress
                         │
             ┌───────────┴───────────┐
             │                       │
       Current State           Supporting Data
             │                       │
             ▼                       ▼
      ┌─────────────┐        TelemetrySidebar
      │ Workspace   │
      │ View        │
      └──────┬──────┘
             │
      ┌──────┼────────┬────────┐
      ▼      ▼        ▼        ▼
    Input  Activity  Decision  Report

## Completed Run

Once the run is complete, the user can manually inspect:

Overview | Evidence | Changes | PR Draft