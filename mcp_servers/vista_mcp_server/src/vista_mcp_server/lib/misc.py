import re, json
from datetime import timedelta


def parse_time_limit(s: str):
    """Parse a time delta string in 'h:mm:ss' format."""
    try:
        hours, minutes, seconds = s.split(":")
        return timedelta(hours=int(hours), minutes=int(minutes), seconds=int(seconds))
    except:
        raise ValueError(f"Invalid time limit: {s}")


def validate_job_id(job_id: str):
    """Validate and normalise an S3M job ID (string, e.g. "job-12345" or "12345")."""
    job_id = job_id.strip()
    if not re.fullmatch(r"[\w-]+", job_id):
        raise ValueError(f"Invalid job id {job_id!r}")
    return job_id


def get_tool_call_string(tool: str, /, **kwargs):
    kwargs = {k: v for k, v in kwargs.items() if v != None}
    if kwargs:
        return (
            f"{tool}(\n" +
            ',\n'.join(f"  {k}={json.dumps(v)}" for k, v in kwargs.items()) +
            "\n)"
        )
    else:
        return f"{tool}()"
