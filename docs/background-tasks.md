# Background work

The agent can start work that keeps going after its turn ends: a long command in its sandbox,
or a watch on a job it submitted to a cluster. You can keep talking to it meanwhile, and VISTA
tells the conversation when the work ends, starting a turn for it if none is running.

## Background commands

Ask for a long command "in the background", or let the agent decide to run one there. The agent
gets a task id such as `bg-3` at once and its turn can end; the command's output goes to a log
under `/mnt/data/tasks/`, which the agent can read while it runs.

When the command ends, its conversation receives one result: how it ended (its exit status,
stopped, or timed out), its runtime, and the last lines of its output. Any exit status is
reported, not only success.

- A command runs for 1 hour unless the agent asks for longer, and 24 hours at most.
- A conversation can have 4 commands running at once.
- A log stops growing at 10 MB; the command keeps running.

## Job watches

After submitting a job to Odo, Frontier, Lux or Perlmutter, the agent can ask VISTA to watch
it. The conversation then hears once when the job starts running and once when it ends,
whatever its final state, so neither you nor the agent has to keep asking.

- A watch reads the job's state every 30 seconds for the first 10 minutes after it starts, and
  for the first 10 minutes after the job starts running. In between, the interval grows to 5
  minutes. If the facility asks VISTA to slow down, it waits as long as asked.
- A failed state is reported only when a second reading confirms it.
- A conversation can watch 8 jobs at once.
- Stopping a watch leaves the job running on its cluster.

### When cluster access lapses

If your token for the cluster is missing or has expired, the watch pauses. The conversation
shows the "needs you" dot, and the Background tab says what to reconnect, for example "Paused:
Reconnect Odo in Settings". No agent turn starts for it. Save the token again in Settings and
the watch reads the job at once and carries on; a job that ended in the meantime is reported
then.

## Messages while the agent works

The message box stays usable during a turn. A message you send while the agent is busy waits
as a **Queued** chip above the box, and the agent receives it at its next step, in the same
turn. Until then you can edit or delete it. **Stop** ends the turn and puts any queued text back
in the message box, unsent.

## The Background tab

Once a conversation has a background task, a **Background** tab appears in the workspace
column, with the number running on its label. Each task shows its state and runtime, a **Log**
button for a command, and **Stop** while it runs: Stop ends a command and everything it
started, or drops a watch without touching its job.

## Turns VISTA starts

A result that arrives while no turn runs starts one. It opens with a one-line row naming the
event, such as "bg-3 finished · exit 0 · 4m 2s", which expands to the full result, followed by
the agent's reply. If the conversation is open, the turn is drawn as it runs.

VISTA holds these turns back so they cannot run away:

- At most 10 in a row without a message from you. Further results wait, and the conversation
  shows "needs you" until you write; your message then carries them.
- After you press **Stop**, results wait for your next message rather than starting turns.

## Quitting and deleting

Watches survive quitting VISTA: when it starts again, each one reads its job at once, and a
job that ended while VISTA was closed is reported. Background commands do not survive a quit;
each is recorded as interrupted, and the agent is told with your next message there.

Deleting a conversation that still has a command running or a watch active asks first, naming
them. Confirming ends the commands and drops the watches, and the logs go with the
conversation. Jobs on the clusters are left as they are.
