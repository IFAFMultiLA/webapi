# Codebook for MultiLA web API data export

This data export archive contains three files in CSV format, all of which can be joined via common identifiers that
are documented below and highlighted in **bold.**

## File `app_sessions.csv`

Contains data on application sessions, i.e. information on applications and the configured sessions that can be
visited by users.

- `app_id`: ID of the application – integer
- `app_name`: name of the application – character string
- `app_url`: URL where the application was served – character string
- `app_config_id`: ID of the application configuration – integer
- `app_config_label`: name of the application configuration – character string
- **`app_sess_code`: session code of the application session (session code for a configured application that was shared
                     to the users) – character string**
- `app_sess_auth_mode`: authentication mode of the application session – categorical; `"none"` or `"login"`

## File `tracking_sessions.csv`

Contains data on user and tracking sessions, i.e. information on users and their interaction sessions with the
applications starting with the first visit of an application session and ending with closing the browser window.

- **`app_sess_code`: session code of the application session (session code for a configured application that was shared
                     to the users) – character string**
- `user_app_sess_code`: user application session code (session code for an individual anonymous or registered user
                        interacting with a specific application session) – character string
- `user_app_sess_user_id`: user ID for registered users; no further data on individual users is provided in this 
                           dataset – integer for registered users or NA for anonymous users
- **`track_sess_id`: tracking session ID (ID indicating for a continuous interaction of a user with the application
                     session on a single device) – integer**
- `track_sess_start`: start of the tracking session (first visit of a user on this device for this application session)
                      – UTC date and time in format `Y-M-D H:M:S`
- `track_sess_end`: end of the tracking session (user closes the browser window of logs out) – UTC date and time in
                    format `Y-M-D H:M:S`
- `track_sess_device_info`: information on the device used by the user in this tracking session – JSON with the
                            following information:
  - `user_agent`: "user agent" string from the browser – character string
  - `form_factor`: categorical; `"desktop"`, `"tablet"` or `"phone"`
  - `window_size`: array with two elements as integers: `[window width, window height]`

## File `tracking_events.csv`

Contains data on events produced by users within a tracking session.

- **`track_sess_id`: tracking session ID (ID indicating for a continuous interaction of a user with the application
                     session on a single device) – integer**
- `event_time`: time when the event took place – UTC date and time in format `Y-M-D H:M:S`
- `event_type`: type of the event – categorical; `"device_info_update"`, `"learnr_event_*"` (see below for possible
                *learnr* events in `*` placeholder) or `"mouse"`
- `event_value`: event data – JSON; depends on `event_type`:
  - for `"device_info_update"`: changed window size as `{"window_size": [width, height]}`
  - for `"learnr_event_*"`: data depends on learnr event type (see below)
  - for `"mouse"`: raw mouse tracking data as collected with [mus.js](https://github.com/ineventapp/musjs); data is
    collected in chunks and must be concatenated to form the trace for the whole tracking session
    - `frames`: array with mouse interactions; each item is an array `[type, x, y, xpath, timestamp]`
      - `type` can be: `"m"` – move; `"c"` – click; `"s"` – scroll; `"i"` – key input; `"o"` – input value change
        (sliders, checkboxes, etc.)
      - `x` and `y` are cursor positions within the window
      - `xpath` is the XPath for the current element or `null` if the element is the same as in the previous record
      - `timestamp` is the time in ms
    - `window`: window size
    - `timeElapsed`: time in ms since mouse tracking started

### Learnr events

- `exercise_hint`: User requested a hint or solution for an exercise.
- `exercise_submitted`: User submitted an answer for an exercise.
- `exercise_result`: The evaluation of an exercise has completed.
- `question_submission`: User submitted an answer for a multiple-choice question.
- `video_progress`: User watched a segment of a video.
- `section_skipped`: A section of the tutorial was skipped.
- `section_viewed`: A section of the tutorial became visible.
