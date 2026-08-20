from tam.events.bus import EventBus
from tam.events.types import Event


def test_publish_calls_subscribed_handlers():
    bus = EventBus()
    received = []
    bus.subscribe("topic-a", received.append)

    event = Event(type="topic-a", payload=123)
    bus.publish("topic-a", event)

    assert received == [event]


def test_publish_ignores_other_topics():
    bus = EventBus()
    received = []
    bus.subscribe("topic-a", received.append)
    bus.publish("topic-b", Event(type="topic-b"))
    assert received == []


def test_multiple_subscribers_all_receive_event():
    bus = EventBus()
    a, b = [], []
    bus.subscribe("topic", a.append)
    bus.subscribe("topic", b.append)
    event = Event(type="topic")
    bus.publish("topic", event)
    assert a == [event]
    assert b == [event]
