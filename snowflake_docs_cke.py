# For this app to run, ensure you have installed
# the 'snowflake' package using the package picker above

import streamlit as st
from snowflake.core import Root # requires snowflake>=0.8.0
from snowflake.snowpark.context import get_active_session

MODELS = [
    "mistral-large",
    "snowflake-arctic",
    "llama3-70b",
    "llama3-8b",
]


def init_messages():
    """
    Initialize the session state for chat messages. If the session state indicates that the
    conversation should be cleared or if the "messages" key is not in the session state,
    initialize it as an empty list.
    """
    if st.session_state.clear_conversation or "messages" not in st.session_state:
        st.session_state.messages = []

def init_service_metadata():
    """
    Initialize the session state for cortex search service metadata. Query the available
    cortex search services from the Snowflake session and store their names and search
    columns in the session state.
    """

    
    if "service_metadata" not in st.session_state:
        services = session.sql("SHOW CORTEX SEARCH SERVICES IN snowflake_documentation.shared;").collect()
        service_metadata = []
        if services:
            for s in services:
                svc_name = s["name"]
                svc_search_col = session.sql(
                    f"DESC CORTEX SEARCH SERVICE snowflake_documentation.shared.{svc_name};"
                ).collect()[0]["search_column"]
                service_metadata.append(
                    {"name": svc_name, "search_column": svc_search_col}
                )

        st.session_state.service_metadata = service_metadata

def init_config_options():
    """
    Initialize the configuration options in the Streamlit sidebar. Allow the user to select
    a cortex search service, clear the conversation, toggle debug mode, and toggle the use of
    chat history. Also provide advanced options to select a model, the number of context chunks,
    and the number of chat messages to use in the chat history.
    """
    service_names = [s["name"] for s in st.session_state.service_metadata]
    
    default_index = service_names.index("CKE_SNOWFLAKE_DOCS_SERVICE") if "CKE_SNOWFLAKE_DOCS_SERVICE" in service_names else 0
    
    st.sidebar.selectbox(
        "Select Cortex Knowledge Extension:",
        service_names,
        index=default_index,
        key="selected_cortex_search_service",
    )

    st.sidebar.button("Clear conversation", key="clear_conversation")
    st.sidebar.toggle("Debug", key="debug", value=False)
    st.sidebar.toggle("Use chat history", key="use_chat_history", value=True)

    with st.sidebar.expander("Advanced options"):
        st.selectbox("Select model:", MODELS, key="model_name")
        st.number_input(
            "Select number of context chunks",
            value=5,
            key="num_retrieved_chunks",
            min_value=1,
            max_value=10,
        )
        st.number_input(
            "Select number of messages to use in chat history",
            value=5,
            key="num_chat_messages",
            min_value=1,
            max_value=10,
        )

    # st.sidebar.expander("Session State").write(st.session_state)

def query_cortex_search_service(query):
    """
    Query the selected cortex search service with the given query and retrieve context documents.
    Display the retrieved context documents in the sidebar if debug mode is enabled. 
    Return the context documents as a string along with citation information.

    Args:
        query (str): The query to search the cortex search service with.

    Returns:
        tuple: (context_str, citations) where context_str is the concatenated string of context documents
              and citations is a list with a single citation from the first result.
    """
    db, schema = 'snowflake_documentation', 'shared'

    cortex_search_service = (
        root.databases[db]
        .schemas[schema]
        .cortex_search_services[st.session_state.selected_cortex_search_service]
    )

    # Modify to retrieve additional columns for citations
    context_documents = cortex_search_service.search(
        query, 
        columns=["chunk", "document_title", "source_url"], 
        limit=st.session_state.num_retrieved_chunks
    )
    results = context_documents.results

    service_metadata = st.session_state.service_metadata
    search_col = [s["search_column"] for s in service_metadata
                    if s["name"] == st.session_state.selected_cortex_search_service][0]

    context_str = ""
    citations = []
    
    if st.session_state.debug:
        st.write("Available keys in first result:", list(results[0].keys()) if results else "No results")
        st.write("Expected search column:", search_col)
    
    for i, r in enumerate(results):
        # Add debug output
        if st.session_state.debug:
            st.write(f"Result {i+1}:", r)
        
        # Try to get the content using the search column name
        content = None
        for col_name in [search_col, "chunk", "CHUNK", "content", "CONTENT"]:
            if col_name in r:
                content = r[col_name]
                break
        
        if content is None:
            if st.session_state.debug:
                st.error(f"Could not find content in result {i+1}. Available keys: {list(r.keys())}")
            content = f"Content not found - available keys: {list(r.keys())}"
        
        # Add to context string
        context_str += f"Context document {i+1}: {content} \n" + "\n"
    
    # Only create one citation from the first result
    if results:
        first_result = results[0]
        citations = [{
            "index": 1,
            "title": first_result.get("document_title", "Unknown Title"),
            "source": first_result.get("source_url", "Unknown Source")
        }]

    if st.session_state.debug:
        st.sidebar.text_area("Context documents", context_str, height=500)

    return context_str, citations

def get_chat_history():
    """
    Retrieve the chat history from the session state limited to the number of messages specified
    by the user in the sidebar options.

    Returns:
        list: The list of chat messages from the session state.
    """
    start_index = max(
        0, len(st.session_state.messages) - st.session_state.num_chat_messages
    )
    return st.session_state.messages[start_index : len(st.session_state.messages) - 1]

def complete(model, prompt):
    """
    Generate a completion for the given prompt using the specified model.

    Args:
        model (str): The name of the model to use for completion.
        prompt (str): The prompt to generate a completion for.

    Returns:
        str: The generated completion.
    """
    return session.sql("SELECT snowflake.cortex.complete(?,?)", (model, prompt)).collect()[0][0]

def make_chat_history_summary(chat_history, question):
    """
    Generate a summary of the chat history combined with the current question to extend the query
    context. Use the language model to generate this summary.

    Args:
        chat_history (str): The chat history to include in the summary.
        question (str): The current user question to extend with the chat history.

    Returns:
        str: The generated summary of the chat history and question.
    """
    prompt = f"""
        [INST]
        Based on the chat history below and the question, generate a query that extend the question
        with the chat history provided. The query should be in natural language.
        Answer with only the query. Do not add any explanation.

        <chat_history>
        {chat_history}
        </chat_history>
        <question>
        {question}
        </question>
        [/INST]
    """

    summary = complete(st.session_state.model_name, prompt)

    if st.session_state.debug:
        st.sidebar.text_area(
            "Chat history summary", summary.replace("$", "\$"), height=150
        )

    return summary

def create_prompt(user_question):
    """
    Create a prompt for the language model by combining the user question with context retrieved
    from the cortex search service and chat history (if enabled). Format the prompt according to
    the expected input format of the model.

    Args:
        user_question (str): The user's question to generate a prompt for.

    Returns:
        tuple: (prompt, citations) where prompt is the generated prompt for the language model
              and citations is the list of citation information.
    """
    if st.session_state.use_chat_history:
        chat_history = get_chat_history()
        if chat_history != []:
            question_summary = make_chat_history_summary(chat_history, user_question)
            prompt_context, _ = query_cortex_search_service(question_summary)  # Context from modified query
            _, citations = query_cortex_search_service(user_question)  # Citations from original query
        else:
            prompt_context, citations = query_cortex_search_service(user_question)
    else:
        prompt_context, citations = query_cortex_search_service(user_question)
        chat_history = ""

    prompt = f"""
            [INST]
            You are a helpful AI chat assistant with RAG capabilities. When a user asks you a question,
            you will also be given context provided between <context> and </context> tags. Use that context
            with the user's chat history provided in the between <chat_history> and </chat_history> tags
            to provide a summary that addresses the user's question. Ensure the answer is coherent, concise,
            and directly relevant to the user's question.

            If the user asks a generic question which cannot be answered with the given context or chat_history,
            just say "I don't know the answer to that question." Do not provide any citations at all, ever, in this case.

            Don't say things like "according to the provided context".

            <chat_history>
            {chat_history}
            </chat_history>
            <context>
            {prompt_context}
            </context>
            <question>
            {user_question}
            </question>
            [/INST]
            Answer:
        """
    return prompt, citations

def main():
    st.title(f":snowflake: Chat With Snowflake Documentation")

    init_service_metadata()
    init_config_options()
    init_messages()

    icons = {"assistant": "❄️", "user": "👤"}

    # Display chat messages from history on app rerun
    for message in st.session_state.messages:
        with st.chat_message(message["role"], avatar=icons[message["role"]]):
            st.markdown(message["content"])

    disable_chat = (
        "service_metadata" not in st.session_state
        or len(st.session_state.service_metadata) == 0
    )
    if question := st.chat_input("Ask a question...", disabled=disable_chat):
        # Add user message to chat history
        st.session_state.messages.append({"role": "user", "content": question})
        # Display user message in chat message container
        with st.chat_message("user", avatar=icons["user"]):
            st.markdown(question.replace("$", "\$"))

        # Display assistant response in chat message container
        with st.chat_message("assistant", avatar=icons["assistant"]):
            message_placeholder = st.empty()
            question = question.replace("'", "")
            with st.spinner("Thinking..."):
                # Get the prompt and citations from create_prompt
                prompt, citations = create_prompt(question)
                # Only pass the prompt to the complete function
                generated_response = complete(st.session_state.model_name, prompt)

                # Check if the response indicates the question wasn't answered
                no_answer_phrases = [
                    "I don't know the answer",
                    "I cannot answer", 
                    "I'm not sure",
                    "I don't have enough information",
                    "I'm unable to answer",
                    "I cannot provide",
                    "I don't have access to",
                    "I'm not able to",
                    "I cannot find",
                    "I don't understand",
                    "I cannot determine"
                ]
                 # Check if the response contains any of these phrases (case-insensitive)
                response_lower = generated_response.lower()
                has_no_answer = any(phrase.lower() in response_lower for phrase in no_answer_phrases)

            
                # Generate citations table in markdown
                if citations and not has_no_answer:
                    citation_table = "\n\n##### Citation\n\n"
                    citation_table += "| Index | Title | Source |\n"
                    citation_table += "|-------|-------|--------|\n"
                    for citation in citations:
                        citation_table += f"| {citation['index']} | {citation['title']} | {citation['source']} |\n"
                    
                    # Show full response with citation in current message
                    full_response = f"{generated_response}\n{citation_table}"
                    message_placeholder.markdown(full_response)
                else:
                    message_placeholder.markdown(generated_response)

        # Store only the response without citations in session state
        st.session_state.messages.append(
            {"role": "assistant", "content": generated_response}  # No citation table stored
        )

if __name__ == "__main__":
    session = get_active_session()
    root = Root(session)
    main()
