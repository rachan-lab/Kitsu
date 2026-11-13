# TODO

- [x] Refine Ghosty forecasts with previous-streak length and IST weekend inputs,
  validated against later results and retrained as confirmed gambles are recorded.
  - Use historical occurrence percentages and ±2 forecast success internally.

- [x] Add automatic emoji/sticker mood classification using the existing local
  Gemma 3 vision model, with cached results and separate NSFW and nudity estimates.
  - Automatic replies are paused.
  - Custom emoji/sticker names and images are analyzed together for mood context.

- [ ] Reintroduce translation through a cloud-hosted LLM/service when work resumes.
  - Local translation is paused; its cog, training tools, datasets, and model
    artifacts have been removed from this project.
  - Check translation quality, response time, and failure handling before enabling
    the cloud integration.
