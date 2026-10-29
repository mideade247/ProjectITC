# Email Workflow Management & Validation

## Overview

Email Workflow Management & Validation is a Python software project focused on improving the reliability, maintainability, and testing of email-processing workflows.

The existing ProjectITC repository contains an email application with application code, dependency configuration, Docker support, and deployment-related files.

The next development phase aims to extend the application's conventional backend functionality through improved validation, workflow management, retry handling, logging, and automated testing.

## Business Problem

Email-based applications can experience reliability problems when processing requests.

Common challenges include:

- Invalid email addresses
- Missing required fields
- Duplicate requests
- Temporary processing failures
- Inconsistent error handling
- Limited processing history
- Poor workflow visibility
- Insufficient automated testing

These problems can lead to failed operations, duplicated processing, and increased maintenance effort.

This project aims to provide a more reliable and transparent email workflow management system.

## Current Project Status

The existing application is located in:

```text
ProjectITC/email_mcp_app/
```

The directory includes:

- Python application code
- Dependency configuration
- Dockerfile
- Deployment scripts
- Kubernetes configuration
- Application documentation

The additional validation, persistence, retry, and workflow-tracking functionality described below is planned development work.

## Planned Features

### 1. Email Input Validation

- Validate email address formats.
- Check required message fields.
- Validate subject and message content.
- Reject malformed requests.
- Return consistent validation errors.

### 2. Workflow Management

- Track email-processing requests.
- Record workflow status.
- Detect duplicate submissions.
- Support dry-run execution.
- Maintain processing history.

### 3. Reliability Improvements

- Implement configurable retry policies.
- Handle temporary processing failures.
- Record failure reasons.
- Introduce structured logging.
- Improve error handling.

### 4. Database Integration

- Store workflow metadata in SQLite.
- Track request timestamps.
- Record processing outcomes.
- Retrieve workflow history.

### 5. Automated Testing

- Unit tests for validation.
- Integration tests for workflow processing.
- Retry mechanism tests.
- Mock email provider tests.
- Automated CI checks.

## Technology Stack

| Technology | Purpose |
|------------|---------|
| Python 3.11+ | Core application development |
| Pydantic | Request validation |
| SQLite | Workflow data storage |
| SQLAlchemy | Database operations |
| Pytest | Automated testing |
| Docker | Application containerisation |
| GitHub Actions | Continuous integration |
| Git | Version control |

FastAPI may be introduced if an HTTP interface is appropriate for the existing application.

## Proposed Project Structure

```text
email_mcp_app/
├── app.py
├── src/
│   └── email_workflow/
│       ├── __init__.py
│       ├── validation.py
│       ├── templates.py
│       ├── workflow.py
│       ├── persistence.py
│       ├── retry.py
│       └── logging_config.py
├── tests/
│   ├── test_validation.py
│   ├── test_workflow.py
│   ├── test_persistence.py
│   └── test_retry.py
├── examples/
├── k8s/
├── Dockerfile
├── requirements.txt
├── README.md
└── ROADMAP.md
```

## Getting Started

### Prerequisites

- Python 3.11 or later
- Git
- pip
- Docker (optional)

### Clone the Repository

```bash
git clone https://github.com/mideade247/ProjectITC.git
cd ProjectITC/email_mcp_app
```

### Create a Virtual Environment

```bash
python -m venv .venv
```

Activate the environment.

**Windows:**

```bash
.venv\Scripts\activate
```

**Linux/macOS:**

```bash
source .venv/bin/activate
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

## Configuration

The existing application includes an example environment configuration.

Create a local configuration file from the example:

**Linux/macOS:**

```bash
cp .env.example .env
```

**Windows PowerShell:**

```powershell
Copy-Item .env.example .env
```

Review the example configuration and supply only the settings required by the application.

Never commit real credentials or authentication tokens.

## Planned Workflow

The proposed workflow will follow these steps:

1. Receive an email-processing request.
2. Validate the request.
3. Check for duplicate processing.
4. Create a workflow record.
5. Execute the workflow or perform a dry run.
6. Record the processing result.
7. Retry eligible temporary failures.
8. Return the final workflow status.

## Development Roadmap

| Phase | Development Task | Estimated Hours |
|-------|------------------|-----------------|
| 1 | Review existing application and isolate non-AI functionality | 5 |
| 2 | Implement email-input validation | 5 |
| 3 | Develop workflow status tracking | 6 |
| 4 | Add SQLite persistence and duplicate detection | 6 |
| 5 | Implement retry handling, dry runs, and logging | 6 |
| 6 | Build automated tests | 6 |
| 7 | Improve documentation and CI | 3 |
| **Total** | | **37 hours** |

## Testing

Once the proposed test suite is implemented:

```bash
pytest
```

Testing will cover:

- Valid email requests
- Invalid email addresses
- Missing required fields
- Duplicate processing
- Temporary failures
- Retry behaviour
- Workflow persistence
- Error handling

## Security and Privacy

- Use synthetic email addresses for testing.
- Do not commit credentials or API tokens.
- Do not commit real email content.
- Use mock email providers during automated testing.
- Keep sensitive configuration in environment variables.
- Ensure real email delivery requires explicit configuration and authorisation.

## Development Scope

The planned work focuses on conventional software engineering:

- Input validation
- Backend application development
- Workflow management
- Database operations
- Reliability engineering
- Automated testing
- Deployment improvements

AI model training, fine-tuning, evaluation, benchmarking, and inference are outside the proposed development scope.

## Future Improvements

- Workflow monitoring dashboard
- Advanced retry policies
- Scheduled workflow execution
- Improved processing history
- Additional validation rules
- Expanded automated testing

## License

