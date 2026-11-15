"""How much time is left before a slot's application deadline, as people read it.

Days when two or more days are left, otherwise hours, and minutes when less than one hour is left. The same rule is applied in the browser by
``static/js/countdown.js`` (``IPT.timeLeftText``) so server-rendered and script-rendered cards agree.
Only slot deadlines use this; payment and cancel-window countdowns keep their exact format.
"""

from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext


def time_left_text(deadline, now=None):
    if deadline is None:
        return _("No deadline")
    seconds = (deadline - (now or timezone.now())).total_seconds()
    if seconds <= 0:
        return _("Applications closed")
    if seconds < 3600:
        minutes = max(1, int(seconds // 60))
        return ngettext("%(n)d minute left", "%(n)d minutes left", minutes) % {"n": minutes}
    if seconds >= 2 * 86400:
        days = int(seconds // 86400)
        return ngettext("%(n)d day left", "%(n)d days left", days) % {"n": days}
    hours = int(seconds // 3600)
    return ngettext("%(n)d hour left", "%(n)d hours left", hours) % {"n": hours}
