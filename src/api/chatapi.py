class _ChatAPIBase:
    def __init__(self, provider, model, apikey, **opts):
        self._provider = provider
        self._model = model
        self._apikey = apikey
        self._opts = opts

    def request(self, messages, **kwargs):
        raise NotImplementedError


class OpenAIChatAPI(_ChatAPIBase):
    def __init__(self, provider, model, apikey, **opts):
        from openai import OpenAI

        super().__init__(provider, model, apikey, **opts)
        self._client = OpenAI(api_key=self._apikey, **self._opts)

    def request(self, messages, **kwargs):
        completion = self._client.chat.completions.create(model=self._model, messages=messages, **kwargs)

        # get the API response
        if not completion.choices:
            return None

        return completion.choices[0].message.content


def new_chat_api(provider, model, apikey, **opts):
    if provider == "openai":
        return OpenAIChatAPI(provider, model, apikey, **opts)

    raise ValueError(f'provider "{provider}" not supported')
