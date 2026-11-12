# background-tasks Specification

## Purpose
Let the agent start work that outlives its turn, a command in the sandbox or a watch on a
cluster job, and define what that work reports, how it is limited, listed and stopped, and
what becomes of it when VISTA restarts, the agent is rebuilt, or the conversation is deleted.

## Requirements

### Requirement: A command can be run in the background

The agent SHALL be able to start a sandbox command in the background. The start SHALL return
without waiting for the command, with a task id and the path of the task's log. The turn that
started it SHALL be able to end while the command runs. A command run without the background
option SHALL behave as before.

#### Scenario: Start returns at once

- **WHEN** the agent starts `sleep 60` in the background
- **THEN** the tool call returns within a few seconds with a task id and a log path
- **AND** the task is listed as running

#### Scenario: Foreground is unchanged

- **WHEN** the agent runs a command without the background option
- **THEN** the call returns the command's output when it exits, as before

### Requirement: Background output is kept in a log

A background command's standard output and standard error SHALL be written, combined, to a
log file on the project volume that the agent can read with its file tools while the command
runs and after it ends. A log SHALL stop growing at 10 MB and SHALL then end with a line saying
output was cut off; the command SHALL keep running.

#### Scenario: Reading a running task's log

- **WHEN** a background command has printed three lines and is still running
- **THEN** reading its log shows those three lines

#### Scenario: Log reaches its size limit

- **WHEN** a background command writes more than 10 MB of output
- **THEN** its log is no larger than 10 MB plus a final line saying output was cut off
- **AND** the command runs to its own exit

### Requirement: A command's end is reported to its conversation

When a background command ends, its conversation SHALL receive one result naming the task,
its command, how it ended (exit status, stopped, or timed out), its runtime, its log path, and
the last lines of its output. Any exit status SHALL be reported, not only success.

#### Scenario: Non-zero exit is reported

- **WHEN** a background command exits with status 7
- **THEN** its conversation receives a result saying the task exited with status 7

### Requirement: A background command has a time limit

A background command SHALL be ended when it reaches its time limit and SHALL be recorded as
timed out. The limit SHALL be 1 hour unless the agent sets one, and a requested limit above
24 hours SHALL be refused.

#### Scenario: Command exceeds its limit

- **WHEN** a background command started with a 2-second limit is still running after it
- **THEN** the command is ended, the task is recorded as timed out, and the result says so

#### Scenario: Limit above the ceiling

- **WHEN** the agent asks for a 48-hour limit
- **THEN** the start is refused with a message naming the 24-hour ceiling

### Requirement: The agent can watch a cluster job

The agent SHALL be able to ask VISTA to watch a submitted job on Odo, Frontier, Lux or
Perlmutter. A watch SHALL report to its conversation once when the job starts running and once
when it reaches a final state, whichever final state that is. No job SHALL be watched unless a
watch was asked for. A failed state SHALL be reported only when a second reading confirms it.

#### Scenario: Start and end are reported

- **WHEN** a watched job goes from queued to running to completed
- **THEN** the conversation receives one result for the start and one for the end

#### Scenario: Failure is reported

- **WHEN** a watched job is read as failed on two successive readings
- **THEN** the conversation receives a result naming the failed state

#### Scenario: A single failed reading is not believed

- **WHEN** a watched job is read once as failed and next as running
- **THEN** no failure is reported

#### Scenario: A job that never ran

- **WHEN** a watched job is cancelled while queued
- **THEN** the conversation receives one result, for the end

### Requirement: Watch polling is paced

A watch SHALL read its job's state every 30 seconds for the first 10 minutes after the watch
starts and for the first 10 minutes after the job starts running, and otherwise SHALL lengthen
the interval up to 5 minutes. When the facility answers that requests are too frequent, the
watch SHALL wait at least as long as the facility asks before its next reading.

#### Scenario: Long queue wait

- **WHEN** a watched job has been queued for an hour
- **THEN** readings are 5 minutes apart

#### Scenario: Facility asks for a pause

- **WHEN** a reading is answered with "too many requests, retry after 120 seconds"
- **THEN** the next reading happens no sooner than 120 seconds later

### Requirement: A watch outlives a restart of VISTA

Job watches SHALL be saved. When VISTA starts, every watch whose job had not reached a final
state SHALL resume, and a change that happened while VISTA was closed SHALL be reported as it
would have been live.

#### Scenario: Job finished while VISTA was closed

- **WHEN** VISTA is reopened after a watched job completed
- **THEN** the conversation receives the result for the job's end

### Requirement: Lost cluster access pauses a watch

When a watch cannot read its job because the researcher's cluster credentials are missing or
expired, the watch SHALL pause, SHALL show in the task list which cluster needs reconnecting,
and SHALL NOT report to the agent. It SHALL resume without further action once the credentials
work again. A paused watch SHALL NOT be reported as a finished or failed job.

#### Scenario: Token expires during a queue wait

- **WHEN** a watch's reading fails because the researcher's token for the cluster has expired
- **THEN** the watch is shown as paused, naming the cluster, and no agent turn starts

#### Scenario: Researcher reconnects

- **WHEN** the researcher reconnects the cluster in Settings
- **THEN** the watch resumes and later changes are reported

### Requirement: Tasks are limited per conversation

A conversation SHALL have at most 4 background commands running and at most 8 job watches
active. A start beyond a limit SHALL be refused with a message telling the agent to wait for
one to end or to stop one.

#### Scenario: Fifth command

- **WHEN** a conversation has 4 background commands running and the agent starts another
- **THEN** the start is refused and names the limit
- **AND** the 4 running commands are unaffected

### Requirement: The agent can list and stop its tasks

The agent SHALL be able to list its conversation's tasks, with each task's id, kind, state and
runtime, and to stop one by id. Stopping a job watch SHALL end the watch and SHALL NOT cancel
the job.

#### Scenario: Stopping a command

- **WHEN** the agent stops a running background command
- **THEN** the task is recorded as stopped and the command is no longer running

#### Scenario: Dropping a watch

- **WHEN** the agent stops a job watch
- **THEN** the watch ends and the job's state on the cluster is unchanged

### Requirement: Tasks survive a rebuild of the agent

A change to Settings or to a conversation's model SHALL take effect on that conversation's
next turn and SHALL NOT end its running background commands. After the change the agent SHALL
still be able to list those tasks, read their logs, stop them, and receive their results.

#### Scenario: Model changed during a long command

- **WHEN** a researcher changes a conversation's model while a background command runs
- **THEN** the next turn uses the new model
- **AND** the command runs to its end and its result is delivered

### Requirement: A command lost to a restart is recorded as interrupted

A background command SHALL NOT survive VISTA quitting. When VISTA next starts, each such
command SHALL be recorded as interrupted, and its conversation SHALL be told with the
researcher's next message there. No turn SHALL be started at launch for it.

#### Scenario: VISTA quits during a command

- **WHEN** VISTA is reopened after quitting while a background command ran
- **THEN** the task is listed as interrupted and no turn starts by itself
- **AND** the agent is told of the interruption at the start of the next turn the researcher
  sends

### Requirement: Tasks and logs last as long as their conversation

Finished tasks and their logs SHALL be kept until their conversation is deleted. Deleting a
conversation that has running commands or active watches SHALL first ask the researcher to
confirm, naming what is running; on confirmation the commands SHALL be ended, the watches
dropped, and the logs removed. Jobs on the cluster SHALL be unaffected.

#### Scenario: Deleting a conversation with a running command

- **WHEN** a researcher deletes a conversation that has a background command running
- **THEN** a confirmation names the running command
- **AND** on confirming, the command is ended and the conversation is deleted

#### Scenario: Deleting a conversation with nothing running

- **WHEN** a researcher deletes a conversation whose tasks have all ended
- **THEN** no extra confirmation about tasks is shown and the logs are removed
