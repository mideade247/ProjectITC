from mcp.server.fastmcp import FastMCP
from typing import List, Optional
from datetime import date, datetime
import json
import os
import uuid

# ── Persistence ──────────────────────────────────────────────────────────────

DATA_FILE = os.path.join(os.path.dirname(__file__), "employees.json")

DEFAULT_BALANCES = {"vacation": 15, "sick": 10, "personal": 5}
LEAVE_TYPES = {"vacation", "sick", "personal"}


def _seed_data() -> dict:
    data = {
        "E001": {"name": "Alice Johnson",  "balances": {"vacation": 15, "sick": 10, "personal": 5}, "leaves": []},
        "E002": {"name": "Bob Smith",      "balances": {"vacation": 12, "sick": 10, "personal": 3}, "leaves": []},
        "E003": {"name": "Carol Williams", "balances": {"vacation": 15, "sick":  8, "personal": 5}, "leaves": []},
        "E004": {"name": "David Brown",    "balances": {"vacation": 10, "sick": 10, "personal": 5}, "leaves": []},
        "E005": {"name": "Emma Davis",     "balances": {"vacation": 15, "sick": 10, "personal": 4}, "leaves": []},
        "E006": {"name": "Frank Miller",   "balances": {"vacation": 14, "sick":  9, "personal": 5}, "leaves": []},
        "E007": {"name": "Grace Wilson",   "balances": {"vacation": 15, "sick": 10, "personal": 5}, "leaves": []},
        "E008": {"name": "Henry Taylor",   "balances": {"vacation": 11, "sick": 10, "personal": 2}, "leaves": []},
        "E009": {"name": "Iris Anderson",  "balances": {"vacation": 15, "sick": 10, "personal": 5}, "leaves": []},
        "E010": {"name": "Jack Thomas",    "balances": {"vacation": 13, "sick":  7, "personal": 5}, "leaves": []},
    }
    _save(data)
    return data


def _load() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return _seed_data()


def _save(data: dict) -> None:
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


employees = _load()

# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_dates(dates: List[str]) -> Optional[str]:
    """Return an error string if any date is invalid, a weekend, or in the past."""
    today = date.today()
    for d in dates:
        try:
            parsed = date.fromisoformat(d)
        except ValueError:
            return f"Invalid date format: '{d}'. Use YYYY-MM-DD."
        if parsed < today:
            return f"Date {d} is in the past."
        if parsed.weekday() >= 5:
            day_name = "Saturday" if parsed.weekday() == 5 else "Sunday"
            return f"Date {d} is a {day_name}. Weekends are not allowed."
    return None


def _get_all_approved_dates(emp: dict) -> set:
    """Return all dates that are already in an approved/pending leave."""
    taken = set()
    for leave in emp["leaves"]:
        if leave["status"] in ("approved", "pending"):
            taken.update(leave["dates"])
    return taken


# ── MCP Server ────────────────────────────────────────────────────────────────

mcp = FastMCP("LeaveManager")


# ── Leave tools ───────────────────────────────────────────────────────────────

@mcp.tool()
def get_leave_balance(employee_id: str, leave_type: str = "") -> str:
    """Check remaining leave balance for an employee.

    Args:
        employee_id: The employee ID (e.g. E001)
        leave_type: Optional – one of 'vacation', 'sick', 'personal'.
                    Leave blank to see all balances.
    """
    emp = employees.get(employee_id)
    if not emp:
        return "Employee ID not found."

    balances = emp["balances"]
    if leave_type:
        if leave_type not in LEAVE_TYPES:
            return f"Unknown leave type '{leave_type}'. Choose from: {', '.join(LEAVE_TYPES)}."
        return f"{emp['name']} ({employee_id}) – {leave_type} balance: {balances[leave_type]} day(s)."

    lines = [f"{emp['name']} ({employee_id}) leave balances:"]
    for lt, bal in balances.items():
        lines.append(f"  {lt.capitalize()}: {bal} day(s)")
    return "\n".join(lines)


@mcp.tool()
def apply_leave(employee_id: str, leave_dates: List[str], leave_type: str) -> str:
    """Apply for leave on specific dates.

    Args:
        employee_id: The employee ID (e.g. E001)
        leave_dates: List of dates in YYYY-MM-DD format (e.g. ["2025-06-01", "2025-06-02"])
        leave_type: One of 'vacation', 'sick', 'personal'
    """
    emp = employees.get(employee_id)
    if not emp:
        return "Employee ID not found."

    if leave_type not in LEAVE_TYPES:
        return f"Unknown leave type '{leave_type}'. Choose from: {', '.join(LEAVE_TYPES)}."

    err = _validate_dates(leave_dates)
    if err:
        return err

    # Check for overlapping leaves
    taken = _get_all_approved_dates(emp)
    overlap = [d for d in leave_dates if d in taken]
    if overlap:
        return f"Dates already booked or pending: {', '.join(overlap)}."

    requested = len(leave_dates)
    if emp["balances"][leave_type] < requested:
        return (
            f"Insufficient {leave_type} balance. "
            f"Requested {requested} day(s) but only {emp['balances'][leave_type]} available."
        )

    leave_id = str(uuid.uuid4())[:8].upper()
    emp["leaves"].append({
        "id": leave_id,
        "dates": sorted(leave_dates),
        "type": leave_type,
        "status": "pending",
        "applied_on": date.today().isoformat(),
        "rejection_reason": "",
    })
    _save(employees)

    return (
        f"Leave request {leave_id} submitted for {requested} {leave_type} day(s) "
        f"on {', '.join(sorted(leave_dates))}. Status: pending approval."
    )


@mcp.tool()
def get_leave_history(employee_id: str) -> str:
    """Get all leave requests for an employee with their statuses.

    Args:
        employee_id: The employee ID (e.g. E001)
    """
    emp = employees.get(employee_id)
    if not emp:
        return "Employee ID not found."

    leaves = emp["leaves"]
    if not leaves:
        return f"{emp['name']} ({employee_id}) has no leave history."

    lines = [f"Leave history for {emp['name']} ({employee_id}):"]
    for lv in leaves:
        reason = f" | Reason: {lv['rejection_reason']}" if lv["rejection_reason"] else ""
        lines.append(
            f"  [{lv['id']}] {lv['type'].capitalize()} | "
            f"{', '.join(lv['dates'])} | Status: {lv['status']}{reason}"
        )
    return "\n".join(lines)


# ── Approval workflow ─────────────────────────────────────────────────────────

@mcp.tool()
def approve_leave(employee_id: str, leave_id: str) -> str:
    """Approve a pending leave request. Deducts days from the employee's balance.

    Args:
        employee_id: The employee ID (e.g. E001)
        leave_id: The leave request ID (e.g. A1B2C3D4)
    """
    emp = employees.get(employee_id)
    if not emp:
        return "Employee ID not found."

    for lv in emp["leaves"]:
        if lv["id"] == leave_id:
            if lv["status"] != "pending":
                return f"Leave {leave_id} is already '{lv['status']}' and cannot be approved."
            emp["balances"][lv["type"]] -= len(lv["dates"])
            lv["status"] = "approved"
            _save(employees)
            return f"Leave {leave_id} approved. {emp['name']}'s {lv['type']} balance is now {emp['balances'][lv['type']]} day(s)."

    return f"Leave ID '{leave_id}' not found for employee {employee_id}."


@mcp.tool()
def reject_leave(employee_id: str, leave_id: str, reason: str = "") -> str:
    """Reject a pending leave request.

    Args:
        employee_id: The employee ID (e.g. E001)
        leave_id: The leave request ID
        reason: Optional rejection reason
    """
    emp = employees.get(employee_id)
    if not emp:
        return "Employee ID not found."

    for lv in emp["leaves"]:
        if lv["id"] == leave_id:
            if lv["status"] != "pending":
                return f"Leave {leave_id} is already '{lv['status']}' and cannot be rejected."
            lv["status"] = "rejected"
            lv["rejection_reason"] = reason
            _save(employees)
            return f"Leave {leave_id} rejected." + (f" Reason: {reason}" if reason else "")

    return f"Leave ID '{leave_id}' not found for employee {employee_id}."


# ── Admin tools ───────────────────────────────────────────────────────────────

@mcp.tool()
def list_all_employees() -> str:
    """List all employees with their current leave balances."""
    if not employees:
        return "No employees found."

    lines = ["All employees:"]
    for eid, emp in sorted(employees.items()):
        b = emp["balances"]
        lines.append(
            f"  {eid} – {emp['name']} | "
            f"Vacation: {b['vacation']}  Sick: {b['sick']}  Personal: {b['personal']}"
        )
    return "\n".join(lines)


@mcp.tool()
def add_employee(
    employee_id: str,
    name: str,
    vacation: int = 15,
    sick: int = 10,
    personal: int = 5,
) -> str:
    """Add a new employee to the system.

    Args:
        employee_id: Unique employee ID (e.g. E011)
        name: Full name of the employee
        vacation: Starting vacation balance (default 15)
        sick: Starting sick leave balance (default 10)
        personal: Starting personal leave balance (default 5)
    """
    if employee_id in employees:
        return f"Employee {employee_id} already exists."

    employees[employee_id] = {
        "name": name,
        "balances": {"vacation": vacation, "sick": sick, "personal": personal},
        "leaves": [],
    }
    _save(employees)
    return f"Employee {employee_id} ({name}) added with vacation={vacation}, sick={sick}, personal={personal}."


@mcp.tool()
def remove_employee(employee_id: str) -> str:
    """Remove an employee from the system.

    Args:
        employee_id: The employee ID to remove
    """
    if employee_id not in employees:
        return "Employee ID not found."

    name = employees[employee_id]["name"]
    del employees[employee_id]
    _save(employees)
    return f"Employee {employee_id} ({name}) has been removed."


@mcp.tool()
def reset_balance(employee_id: str, leave_type: str, new_balance: int) -> str:
    """Reset the leave balance for a specific leave type.

    Args:
        employee_id: The employee ID
        leave_type: One of 'vacation', 'sick', 'personal'
        new_balance: The new balance to set
    """
    emp = employees.get(employee_id)
    if not emp:
        return "Employee ID not found."
    if leave_type not in LEAVE_TYPES:
        return f"Unknown leave type '{leave_type}'. Choose from: {', '.join(LEAVE_TYPES)}."
    if new_balance < 0:
        return "Balance cannot be negative."

    emp["balances"][leave_type] = new_balance
    _save(employees)
    return f"{emp['name']}'s {leave_type} balance reset to {new_balance} day(s)."


# ── Resources ─────────────────────────────────────────────────────────────────

@mcp.resource("greeting://{name}")
def get_greeting(name: str) -> str:
    """Get a personalized greeting."""
    return f"Hello, {name}! How can I assist you with leave management today?"


@mcp.resource("upcoming-leaves://all")
def get_upcoming_leaves_resource() -> str:
    """List all upcoming approved/pending leaves across all employees."""
    today = date.today().isoformat()
    lines = []
    for eid, emp in sorted(employees.items()):
        for lv in emp["leaves"]:
            if lv["status"] in ("approved", "pending"):
                future_dates = [d for d in lv["dates"] if d >= today]
                if future_dates:
                    lines.append(
                        f"{emp['name']} ({eid}) | {lv['type'].capitalize()} | "
                        f"{', '.join(future_dates)} | {lv['status'].upper()}"
                    )

    if not lines:
        return "No upcoming leaves scheduled."

    return "Upcoming leaves:\n" + "\n".join(f"  {l}" for l in sorted(lines))


@mcp.tool()
def get_upcoming_leaves(employee_id: str = "") -> str:
    """List upcoming approved/pending leaves.

    Args:
        employee_id: Optional – filter by employee ID. Leave blank for all employees.
    """
    today = date.today().isoformat()
    lines = []

    targets = {employee_id: employees[employee_id]} if employee_id else employees
    if employee_id and employee_id not in employees:
        return "Employee ID not found."

    for eid, emp in sorted(targets.items()):
        for lv in emp["leaves"]:
            if lv["status"] in ("approved", "pending"):
                future_dates = [d for d in lv["dates"] if d >= today]
                if future_dates:
                    lines.append(
                        f"{emp['name']} ({eid}) | {lv['type'].capitalize()} | "
                        f"{', '.join(future_dates)} | {lv['status'].upper()}"
                    )

    if not lines:
        return "No upcoming leaves found."
    return "Upcoming leaves:\n" + "\n".join(f"  {l}" for l in sorted(lines))


if __name__ == "__main__":
    mcp.run()
