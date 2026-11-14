"""모든 ORM 모델을 한 번 import해서 Base.metadata에 등록한다(Alembic autogenerate용)."""

from app.models.elevator_status import ElevatorStatus
from app.models.elevators import Elevator
from app.models.lines import Line
from app.models.station_edges import StationEdge
from app.models.station_external_ids import StationExternalId
from app.models.station_lines import StationLine
from app.models.stations import Station
from app.models.train_schedules import TrainSchedule

__all__ = [
    "Line",
    "Station",
    "StationLine",
    "StationExternalId",
    "StationEdge",
    "Elevator",
    "ElevatorStatus",
    "TrainSchedule",
]
