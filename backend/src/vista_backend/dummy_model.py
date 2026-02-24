"""
Dummy model for testing purposes.
"""
from pydantic_ai import ModelResponse, TextPart, ModelMessage, AbstractToolset
from pydantic_ai.models.function import FunctionModel, AgentInfo, DeltaToolCall
from pydantic_ai.messages import ToolCallPart
import json


def get_dummy_model():
    def print_messages(messages: list[ModelMessage]):
        print("Model called with:")
        for msg in messages:
            for part in msg.parts:
                print(f"    {str(part)[:80]}")
        print()

    def parse_content(part, info: AgentInfo):
        content = str(part.content).strip() if hasattr(part, "content") else ""
        tool_names = [t.name for t in info.function_tools]

        if " " in content:
            tool, args = content.split(" ", 1)
            tool, args = tool.strip(), args.strip()
            try:
                args = json.loads(args)
            except:
                tools, args = None, None
        else:
            tool = content
            args = {}
        if tool is not None and args is not None and tool in tool_names:
            return tool, args
        else:
            return None, {}

    def model_func(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        print_messages(messages)

        last = messages[-1]
        for part in last.parts:
            tool, args = parse_content(part, info)
            if tool:
                return ModelResponse(parts=[ToolCallPart(tool_name=tool, args=args)])
        return ModelResponse(parts=[TextPart("I don't know how to do that")])

    async def model_stream_func(messages: list[ModelMessage], info: AgentInfo):
        print_messages(messages)

        last = messages[-1]
        for part in last.parts:
            tool, args = parse_content(part, info)
            if tool:
                yield {0: DeltaToolCall(name=tool, json_args=json.dumps(args))}
                return
        yield "I don't know how to do that"
        
    return FunctionModel(
        model_name="dummy",
        function=model_func,
        stream_function = model_stream_func,
    )
