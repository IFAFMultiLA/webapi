"""
Classes and functions for interacting with a chat provider via a web API.
"""


class _ChatAPIBase:
    """Abstract base class for all chat APIs."""

    def __init__(self, provider, model, apikey, **opts):
        """
        Create a new chat API instance.

        :param provider: Provider name, e.g. 'openai'
        :param model: Model name, e.g. 'gpt-4o'
        :param apikey: API key
        :param opts: Optional arguments passed when creating the chat API instance
        """
        self._provider = provider
        self._model = model
        self._apikey = apikey
        self._opts = opts

    def request(self, messages, **kwargs):
        """
        Make a request to the chat API provider with a list of messages.

        This method needs to be implemented in subclasses.

        :param messages: List of messages
        :param kwargs: Keyword arguments passed to the API call
        """
        raise NotImplementedError


class OpenAIChatAPI(_ChatAPIBase):
    """
    Class for OpenAI based chat APIs.
    """

    def __init__(self, provider, model, apikey, **opts):
        """
        Create a new chat API instance for OpenAI chat.

        :param provider: Provider name, e.g. 'openai'
        :param model: Model name, e.g. 'gpt-4o'
        :param apikey: API key
        :param opts: Optional arguments passed when creating the chat API instance
        """
        from openai import OpenAI

        super().__init__(provider, model, apikey, **opts)
        self._client = OpenAI(api_key=self._apikey, **self._opts)

    def request(self, messages, **kwargs):
        """
        Make a request to the chat API provider with a list of messages.

        :param messages: List of messages
        :param kwargs: Keyword arguments passed to the API call
        """
        completion = self._client.chat.completions.create(model=self._model, messages=messages, **kwargs)

        # get the API response
        if not completion.choices:
            return None

        return completion.choices[0].message.content


def new_chat_api(provider, model, apikey=None, **opts):
    """
    Create a new chat API instance for a given provider and model.

    :param provider: Provider name, e.g. 'openai'
    :param model: Model name, e.g. 'gpt-4o'
    :param apikey: API key
    :param opts: Optional arguments passed when creating the chat API instance
    """
    if provider == "openai":
        return OpenAIChatAPI(provider, model, apikey, **opts)

    raise ValueError(f'provider "{provider}" not supported')
