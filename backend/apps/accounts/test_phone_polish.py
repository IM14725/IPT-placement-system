"""Phone-screen polish fixes from the mobile review (WhatsApp button, account card, labels, tap targets, ...)."""

import pytest

FORM_PAGES = ["/auth/login/", "/auth/register/", "/auth/forgot-password/"]


@pytest.mark.django_db
def test_whatsapp_button_is_bottom_right_on_phones_and_absent_on_form_pages(client):
    home = client.get("/").content.decode()
    float_link = next(l for l in home.split("<a ") if "wa.me/255756322833?text=" in l).split(">")[0]  # opening tag only
    assert "right-3" in float_link and "w-11" in float_link and "sm:left-md" in float_link
    assert "left-3" not in float_link  # no longer pinned to the left edge where it covered content
    for url in FORM_PAGES:
        body = client.get(url).content.decode()
        assert "wa.me/255756322833?text=" not in body, url  # the floating button, not the footer link
        assert "WhatsApp: +255 756 322 833" in body, url  # the footer link stays
