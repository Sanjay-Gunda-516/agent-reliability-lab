from enum import StrEnum


class OrderStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    RETURNED = "returned"


class ReturnStatus(StrEnum):
    REQUESTED = "requested"
    COMPLETED = "completed"
