# New Backend Plan

We are migrating the Next.js app to use a new backend. This will allow us to clean up some of the code, have a standalone
API the we can serve, and make it easier to add things needed for productionizing like sessions, users, database storage, etc.

The current backend is pretty minimal, intended to be require minimal migrations to use it in the frontend. We'll need
to update it in the next steps.


A rough plan of what the final API should look like

- `/projects/...`
    - CRUD on project objects
    - RBAC: Users can be added to projects. All users in the project can edit it. You can only see projects you belong to.
    - Might need a special case for "template" or "default" projects that can be read only but seen by everyone
    - We should either use a UUID as project key, or maybe the name for prettier URLs. We'd need to make sure the name is unique across all users, or include the project owner in the url though.
`- /projects/{project}/data/uploads`
    - CRD on uploads (no modify)
    - This is just for data files that will be included in the project's container image
    - Data will be stored on disk, a single directory containing all uploads for a project.
    - Project will need to have a file path to its uploads dir, or the path will be based on project id.
- `/projects/{project}/data/?`
    - We'll need to add the ability to add "remote" data sources like attaching data from the s3 datalake bucket. Need to think a bit more on how that will look like.
- `/projects/{project}/skills`, `/skills`
    - CRUD on skills
    - Skills will be stored using Blob External Storage pattern (saved on disk under a UUID, store path in DB)
    - RBAC: Skills will need to have owners, by default they will be scoped to project but should be possible to be published to global
    - {project}/skills will create skills that are local to the project. /skills will create/list public skills in the skill hub. There should be a {project}/skills/{skill}/publish that "publishes" a skill. Both global and private skills are stored in the same table in the db, only difference is whether they are linked to a project or not.
- `/projects/{project}/rag/...`
    - Here we'll have endpoints to list, upload, remove, and update files to the rag. You should be able to upload individual files, as well as tars and zips which will be extracted.
    - Any change to the file list will trigger re-indexing in the background. Probably will need a few endpoints to track the indexing progress.
    - There will be endpoints to query the project rag here as well.
- `/projects/{project}/mcp/{server}/{tools,call,...}`
    - Ability to directly list and call tools from MCP servers, as they are configured for the project.
    - Should be basically just pass through to MCP calls (tool format etc.)
    - We'll allow projects to configure arbitrary custom MCP servers, including either remote or running stdio ones in a container.
    - Not sure if we'll manipulate the MCP server list as separate data model via CRUD on this route, or just store it as JSON configuration in the project model, similar to how Claude Code configures its MCP servers.
    - Probably store it as JSON on the project at first for simplicity, and extract out into a separate table later to handle securely storing secrets better.
    - One concern is config is secrets in MCP environment variable and header configs. How to store them securely? (symmetric encryption at rest?)
    - Include support for OAuth MCP servers
    - Make sure to avoid SSRF issues for arbitrary remote MCP servers, and run MCP stdio servers in secure sandboxes.
- `/projects/{project}/mcp/elicitation`
    - For handling MCP elicitation requests
- `/projects/{project}/chat/completions`
    - Completion. Format should be similar to standard OpenAI completion, however it will handle calling tool calls etc, and not finish returning until the agent loop for the turn has completed.
    - Does not store the chat, you have to pass the full history. This is for one off chats that you want to manage in your client.
    - Should accept option "streaming" option (probably part of the POST body similar to openai, that switches between regular REST requests and streaming) (or maybe use a separate /streaming endpoint to make the return types cleaner)
- `/projects/{project}/chat/...`
    - List/Get/Delete chat histories for the project.
- `/projects/{project}/chat/{chat_id}/message`
    - Add message to the chat. Returns the new chat history with message appended.
    - Should accept a streaming option like chat/completions
- `/projects/{project}/chat/vercel`
    - This is meant for the web UI. Uses PydanticAI Vecel Adapter. Would require updating the frontend to use the Vercel AI SDK
- `/projects/{project}/chat/?`
- `/users/...`
    - User settings, etc.

Auth will be handled by the SSO, but we'll still need to store user information and user <-> project associations.
