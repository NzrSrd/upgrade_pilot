from app.models import Customer


def test_customer_validates_email() -> None:
    customer = Customer(id=1, email="a@b.com", nickname=None)
    assert customer.email == "a@b.com"
