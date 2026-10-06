"""ORM models. Importing this package registers every table on ``Base.metadata``."""

from app.db.models.acquisition import AcquisitionAttempt
from app.db.models.catalog import Brand, Category, Product
from app.db.models.listing import Listing, ListingImage, ListingPriceHistory, ListingSnapshot
from app.db.models.market import MarketComparable, MarketStatistic
from app.db.models.monitoring import Alert, AlertDelivery, Watchlist
from app.db.models.opportunity import Opportunity, OpportunityScore
from app.db.models.portfolio import Favorite, InventoryItem, Purchase, Sale, UserAffinity
from app.db.models.seller import Seller
from app.db.models.system import AnalysisJob, SystemState
from app.db.models.user import NotificationSettings, PushSubscription, User, UserPreferences

__all__ = [
    "AcquisitionAttempt",
    "Alert",
    "AlertDelivery",
    "AnalysisJob",
    "Brand",
    "Category",
    "Favorite",
    "InventoryItem",
    "Listing",
    "ListingImage",
    "ListingPriceHistory",
    "ListingSnapshot",
    "MarketComparable",
    "MarketStatistic",
    "NotificationSettings",
    "Opportunity",
    "OpportunityScore",
    "Product",
    "Purchase",
    "PushSubscription",
    "Sale",
    "Seller",
    "SystemState",
    "User",
    "UserAffinity",
    "UserPreferences",
    "Watchlist",
]
