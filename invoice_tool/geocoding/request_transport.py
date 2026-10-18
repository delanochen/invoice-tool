"""Geocoder request behavior with explicit shared-state accessors."""

def fetch_geocoder_json(request_url, *, HTTPError, NOMINATIM_USER_AGENT, Request, TemporaryGeocodingError, URLError, _geocode_lock, json, time, urlopen, _state_get, _state_set):
    pass
    with _geocode_lock:
        wait_seconds = 1.05 - (time.monotonic() - _state_get())
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        request_object = Request(request_url, headers={'User-Agent': NOMINATIM_USER_AGENT, 'Accept': 'application/json', 'Accept-Language': 'en-US,en;q=0.8'})
        try:
            with urlopen(request_object, timeout=8) as response:
                payload = json.loads(response.read().decode('utf-8'))
        except HTTPError as error:
            if error.code == 429 or error.code >= 500:
                raise TemporaryGeocodingError(str(error)) from error
            raise
        except (URLError, TimeoutError, OSError) as error:
            raise TemporaryGeocodingError(str(error)) from error
        finally:
            _state_set(time.monotonic())
    return payload
