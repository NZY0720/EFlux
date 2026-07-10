"""V2 trading gateway: decisions -> risk -> reservations -> venue -> settlement."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from eflux.agents.decision import AgentDecision, OrderRequest, ReplaceRequest
from eflux.market.delivery import DeliveryPosition, OrderPurpose
from eflux.market.ledger import EconomicLedger, usd_for_energy
from eflux.market.product_engine import (
    ProductLimitOrder,
    ProductMatchingEngine,
    ProductTrade,
)
from eflux.market.products import DeliveryInterval, TimeInForce
from eflux.market.settlement import (
    SettlementPrices,
    SettlementResult,
    settle_delivery_position,
)
from eflux.vpp.base import VPPParams
from eflux.vpp.der import Battery
from eflux.vpp.reservations import (
    BalanceReservationBook,
    BatteryReservationBook,
    DispatchableReservationBook,
    FlexibleLoadReservationBook,
    ReservationRejected,
)


@dataclass(frozen=True, slots=True)
class GatewayRiskLimits:
    price_min: Decimal = Decimal("-150")
    price_max: Decimal = Decimal("1000")
    qty_min_kwh: Decimal = Decimal("0.01")
    qty_max_kwh: Decimal = Decimal("1000")
    max_open_orders: int = 256
    max_new_orders_per_decision: int = 20
    credit_limit_usd: Decimal = Decimal("1000")


class GatewayRejected(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RejectedRequest:
    request: OrderRequest
    reason: str


@dataclass(frozen=True, slots=True)
class DecisionExecution:
    accepted_order_ids: tuple[int, ...]
    cancelled_order_ids: tuple[int, ...]
    trades: tuple[ProductTrade, ...]
    rejected: tuple[RejectedRequest, ...]


@dataclass(slots=True)
class ParticipantRuntimeV2:
    participant_id: int
    params: VPPParams
    battery: Battery
    balance_reservations: BalanceReservationBook = field(default_factory=BalanceReservationBook)
    positions: dict[str, DeliveryPosition] = field(default_factory=dict)
    reserved_cash_by_order: dict[int, Decimal] = field(default_factory=dict)
    current_dispatchable_power_kw: float = 0.0
    battery_reservations: BatteryReservationBook = field(init=False)
    dispatchable_reservations: DispatchableReservationBook = field(init=False)
    flex_load_reservations: FlexibleLoadReservationBook = field(
        default_factory=FlexibleLoadReservationBook
    )

    def __post_init__(self) -> None:
        self.battery_reservations = BatteryReservationBook(
            capacity_kwh=self.battery.capacity_kwh,
            max_power_kw=self.battery.max_power_kw,
            eta_rt=self.battery.eta_rt,
            initial_soc_kwh=self.battery.soc_kwh,
        )
        self.dispatchable_reservations = DispatchableReservationBook(
            max_power_kw=self.params.gas_kw_max,
            min_power_kw=self.params.gas_min_kw,
            ramp_kw_per_min=self.params.gas_ramp_kw_per_min,
            initial_power_kw=self.current_dispatchable_power_kw,
        )

    def position(self, interval: DeliveryInterval) -> DeliveryPosition:
        return self.positions.setdefault(interval.interval_id, DeliveryPosition(interval=interval))


class TradingGatewayV2:
    def __init__(
        self,
        *,
        engine: ProductMatchingEngine | None = None,
        ledger: EconomicLedger | None = None,
        limits: GatewayRiskLimits | None = None,
    ) -> None:
        self.engine = engine or ProductMatchingEngine()
        self.ledger = ledger or EconomicLedger()
        self.limits = limits or GatewayRiskLimits()
        self.participants: dict[int, ParticipantRuntimeV2] = {}
        self._requests: dict[int, OrderRequest] = {}
        self._owners: dict[int, int] = {}

    def register_participant(
        self, *, participant_id: int, params: VPPParams, battery: Battery | None = None
    ) -> ParticipantRuntimeV2:
        if participant_id in self.participants:
            raise ValueError(f"participant {participant_id} is already registered")
        runtime = ParticipantRuntimeV2(
            participant_id=participant_id,
            params=params,
            battery=battery
            or Battery(
                capacity_kwh=params.battery_kwh,
                max_power_kw=params.battery_kw_max,
                eta_rt=params.battery_eta_rt,
                soc_kwh=params.battery_kwh * params.battery_initial_soc_frac,
            ),
        )
        self.participants[participant_id] = runtime
        return runtime

    def set_balance_projection(
        self, participant_id: int, interval: DeliveryInterval, net_injection_kwh: float
    ) -> None:
        self._participant(participant_id).balance_reservations.set_projection(
            interval, net_injection_kwh
        )

    def set_flex_load_capacity(
        self, participant_id: int, interval: DeliveryInterval, terminal_kwh: float
    ) -> None:
        self._participant(participant_id).flex_load_reservations.set_capacity(
            interval, terminal_kwh
        )

    def execute_decision(
        self,
        *,
        participant_id: int,
        decision: AgentDecision,
        sim_ts: datetime,
        wall_ts: datetime,
    ) -> DecisionExecution:
        runtime = self._participant(participant_id)
        accepted: list[int] = []
        cancelled: list[int] = []
        trades: list[ProductTrade] = []
        rejected: list[RejectedRequest] = []

        for cancel in decision.cancels:
            order = self.engine.get(cancel.order_id)
            if order is None or order.vpp_id != participant_id:
                continue
            removed = self.engine.cancel(cancel.order_id, sim_ts=sim_ts, wall_ts=wall_ts)
            if removed is not None:
                self._release_unfilled(runtime, removed)
                cancelled.append(removed.order_id)

        for replacement in decision.replaces:
            try:
                oid, replacement_trades = self._replace(
                    runtime, replacement, sim_ts=sim_ts, wall_ts=wall_ts
                )
            except GatewayRejected as exc:
                rejected.append(RejectedRequest(replacement.replacement, str(exc)))
            else:
                accepted.append(oid)
                trades.extend(replacement_trades)

        if len(decision.orders) > self.limits.max_new_orders_per_decision:
            overflow = decision.orders[self.limits.max_new_orders_per_decision :]
            rejected.extend(
                RejectedRequest(order, "exceeds max new orders per decision") for order in overflow
            )
            orders = decision.orders[: self.limits.max_new_orders_per_decision]
        else:
            orders = decision.orders
        for request in orders:
            try:
                oid, order_trades = self._submit(runtime, request, sim_ts=sim_ts, wall_ts=wall_ts)
            except GatewayRejected as exc:
                rejected.append(RejectedRequest(request, str(exc)))
            else:
                accepted.append(oid)
                trades.extend(order_trades)
        return DecisionExecution(tuple(accepted), tuple(cancelled), tuple(trades), tuple(rejected))

    def close_interval(
        self, interval: DeliveryInterval, *, sim_ts: datetime, wall_ts: datetime
    ) -> tuple[int, ...]:
        removed = self.engine.close_interval(interval.interval_id, sim_ts=sim_ts, wall_ts=wall_ts)
        for order in removed:
            self._release_unfilled(self._participant(order.vpp_id), order)
        return tuple(order.order_id for order in removed)

    def record_meter_data(
        self,
        participant_id: int,
        interval: DeliveryInterval,
        *,
        renewable_generation_kwh: float = 0.0,
        load_demand_kwh: float = 0.0,
        curtailed_generation_kwh: float = 0.0,
        unserved_load_kwh: float = 0.0,
    ) -> DeliveryPosition:
        position = self._participant(participant_id).position(interval)
        position.renewable_generation_kwh = renewable_generation_kwh
        position.load_demand_kwh = load_demand_kwh
        position.curtailed_generation_kwh = curtailed_generation_kwh
        position.unserved_load_kwh = unserved_load_kwh
        position.validate()
        return position

    def settle_participant(
        self,
        participant_id: int,
        interval: DeliveryInterval,
        *,
        prices: SettlementPrices,
        occurred_at: datetime,
    ) -> SettlementResult:
        if not interval.is_settleable(occurred_at):
            raise ValueError("delivery interval is not finished")
        runtime = self._participant(participant_id)
        position = runtime.position(interval)
        iid = interval.interval_id
        charge = runtime.battery_reservations.committed_terminal_kwh(iid, "buy")
        discharge = runtime.battery_reservations.committed_terminal_kwh(iid, "sell")
        battery_delivery = runtime.battery.execute_terminal_interval(
            charge_terminal_kwh=charge,
            discharge_terminal_kwh=discharge,
            duration_h=interval.duration_h,
        )
        position.battery_charge_terminal_kwh = charge
        position.battery_discharge_terminal_kwh = discharge
        scheduled_gas = runtime.dispatchable_reservations.scheduled_terminal_kwh(iid)
        gas_power = runtime.dispatchable_reservations.scheduled_power_kw(iid)
        position.dispatchable_generation_kwh = scheduled_gas
        position.flexible_load_demand_kwh = runtime.flex_load_reservations.committed_terminal_kwh(
            iid
        )
        startup_cost = (
            Decimal(str(runtime.params.gas_startup_cost_usd))
            if gas_power > 1e-9 and runtime.current_dispatchable_power_kw <= 1e-9
            else Decimal("0")
        )
        result = settle_delivery_position(
            self.ledger,
            participant_id=participant_id,
            position=position,
            prices=prices,
            occurred_at=occurred_at,
            fuel_cost_per_mwh=Decimal(str(runtime.params.gas_cost_per_mwh)),
            dispatchable_startup_cost_usd=startup_cost,
            battery_degradation_cost_per_mwh_throughput=Decimal(
                str(runtime.params.battery_degradation_cost_per_mwh_throughput)
            ),
            battery_cell_throughput_kwh=Decimal(str(battery_delivery.cell_throughput_kwh)),
        )
        runtime.battery_reservations.settle_interval(iid, ending_soc_kwh=runtime.battery.soc_kwh)
        runtime.dispatchable_reservations.settle_interval(iid, ending_power_kw=gas_power)
        runtime.balance_reservations.settle_interval(iid)
        runtime.flex_load_reservations.settle_interval(iid)
        runtime.current_dispatchable_power_kw = gas_power
        self._drop_interval_orders(participant_id, iid)
        return result

    def available_credit_usd(self, participant_id: int) -> Decimal:
        runtime = self._participant(participant_id)
        reserved = sum(runtime.reserved_cash_by_order.values(), Decimal("0"))
        return (
            Decimal(str(runtime.params.starting_cash_usd))
            + self.ledger.balance(participant_id)
            + self.limits.credit_limit_usd
            - reserved
        )

    def _replace(
        self,
        runtime: ParticipantRuntimeV2,
        replacement: ReplaceRequest,
        *,
        sim_ts: datetime,
        wall_ts: datetime,
    ) -> tuple[int, tuple[ProductTrade, ...]]:
        old = self.engine.get(replacement.order_id)
        if old is None or old.vpp_id != runtime.participant_id:
            raise GatewayRejected("order not found or not owned")
        self._validate_static(
            runtime,
            replacement.replacement,
            replacing=True,
            released_credit_usd=runtime.reserved_cash_by_order.get(old.order_id, Decimal("0")),
        )

        # Simulate release + replacement against deep-copied resource books first.
        trial = copy.deepcopy(runtime)
        self._release_unfilled(trial, old)
        trial_id = self.engine.allocate_order_id()
        try:
            self._reserve(trial, trial_id, replacement.replacement)
        except (ReservationRejected, ValueError) as exc:
            raise GatewayRejected(str(exc)) from exc

        removed = self.engine.cancel(old.order_id, sim_ts=sim_ts, wall_ts=wall_ts)
        if removed is None:
            raise GatewayRejected("order disappeared during replace")
        self._release_unfilled(runtime, removed)
        # The preallocated trial id becomes the real replacement id.
        return self._submit(
            runtime,
            replacement.replacement,
            sim_ts=sim_ts,
            wall_ts=wall_ts,
            order_id=trial_id,
        )

    def _submit(
        self,
        runtime: ParticipantRuntimeV2,
        request: OrderRequest,
        *,
        sim_ts: datetime,
        wall_ts: datetime,
        order_id: int | None = None,
    ) -> tuple[int, tuple[ProductTrade, ...]]:
        self._validate_static(runtime, request)
        oid = self.engine.allocate_order_id() if order_id is None else order_id
        try:
            self._reserve(runtime, oid, request)
        except (ReservationRejected, ValueError) as exc:
            raise GatewayRejected(str(exc)) from exc
        self._requests[oid] = request
        self._owners[oid] = runtime.participant_id
        try:
            result = self.engine.submit(
                interval=request.interval,
                vpp_id=runtime.participant_id,
                side=request.side,
                purpose=request.purpose,
                price=request.price,
                qty=request.qty_kwh,
                sim_ts=sim_ts,
                wall_ts=wall_ts,
                time_in_force=request.time_in_force,
                ttl_sec=request.ttl_sec,
                order_id=oid,
            )
        except Exception as exc:
            self._release_request(runtime, oid, request)
            self._requests.pop(oid, None)
            self._owners.pop(oid, None)
            raise GatewayRejected(str(exc)) from exc

        for trade in result.trades:
            self._apply_trade(trade)
        if result.killed or request.time_in_force != TimeInForce.GOOD_TIL_GATE:
            self._release_request(runtime, oid, request)
        return oid, result.trades

    def _validate_static(
        self,
        runtime: ParticipantRuntimeV2,
        request: OrderRequest,
        *,
        replacing: bool = False,
        released_credit_usd: Decimal = Decimal("0"),
    ) -> None:
        lim = self.limits
        self.engine.register(request.interval)
        if request.price < lim.price_min or request.price > lim.price_max:
            raise GatewayRejected(
                f"price {request.price} outside [{lim.price_min}, {lim.price_max}]"
            )
        if request.qty_kwh < lim.qty_min_kwh or request.qty_kwh > lim.qty_max_kwh:
            raise GatewayRejected(
                f"qty {request.qty_kwh} outside [{lim.qty_min_kwh}, {lim.qty_max_kwh}]"
            )
        open_count = len(self.engine.open_orders_for_vpp(runtime.participant_id))
        if open_count >= lim.max_open_orders and not replacing:
            raise GatewayRejected(f"exceeds {lim.max_open_orders} open orders")
        needed = self._worst_case_cash_debit(request)
        available = self.available_credit_usd(runtime.participant_id) + released_credit_usd
        if needed > available:
            raise GatewayRejected(
                f"cash reservation {needed} USD exceeds available credit {available} USD"
            )

    def _reserve(self, runtime: ParticipantRuntimeV2, order_id: int, request: OrderRequest) -> None:
        qty = float(request.qty_kwh)
        if request.purpose == OrderPurpose.BALANCE:
            runtime.balance_reservations.reserve(
                order_id=order_id,
                interval=request.interval,
                side=request.side,
                terminal_kwh=qty,
            )
        elif request.purpose == OrderPurpose.BATTERY:
            runtime.battery_reservations.reserve(
                order_id=order_id,
                interval=request.interval,
                side=request.side,
                terminal_kwh=qty,
            )
        elif request.purpose == OrderPurpose.DISPATCHABLE:
            runtime.dispatchable_reservations.reserve(
                order_id=order_id, interval=request.interval, terminal_kwh=qty
            )
        elif request.purpose == OrderPurpose.FLEX_LOAD:
            runtime.flex_load_reservations.reserve(
                order_id=order_id, interval=request.interval, terminal_kwh=qty
            )
        else:
            raise ReservationRejected(f"unknown order purpose {request.purpose!r}")
        runtime.reserved_cash_by_order[order_id] = self._worst_case_cash_debit(request)

    def _apply_trade(self, trade: ProductTrade) -> None:
        for order_id, participant_id, side in (
            (trade.buy_order_id, trade.buy_vpp_id, "buy"),
            (trade.sell_order_id, trade.sell_vpp_id, "sell"),
        ):
            runtime = self._participant(participant_id)
            request = self._requests[order_id]
            self._commit_resource(runtime, order_id, request, float(trade.qty))
            runtime.position(trade.interval).record_contract(side=side, qty_kwh=float(trade.qty))
            self._consume_cash_reservation(runtime, order_id, request, trade.qty)
        self.ledger.post_trade(
            buyer_id=trade.buy_vpp_id,
            seller_id=trade.sell_vpp_id,
            price_per_mwh=trade.price,
            qty_kwh=trade.qty,
            occurred_at=trade.sim_ts,
            interval=trade.interval,
            trade_id=str(trade.trade_id),
        )

    def _commit_resource(
        self,
        runtime: ParticipantRuntimeV2,
        order_id: int,
        request: OrderRequest,
        qty: float,
    ) -> None:
        if request.purpose == OrderPurpose.BALANCE:
            runtime.balance_reservations.commit_fill(order_id, qty)
        elif request.purpose == OrderPurpose.BATTERY:
            runtime.battery_reservations.commit_fill(order_id, qty)
        elif request.purpose == OrderPurpose.DISPATCHABLE:
            runtime.dispatchable_reservations.commit_fill(order_id, qty)
        elif request.purpose == OrderPurpose.FLEX_LOAD:
            runtime.flex_load_reservations.commit_fill(order_id, qty)

    def _release_unfilled(self, runtime: ParticipantRuntimeV2, order: ProductLimitOrder) -> None:
        request = self._requests.get(order.order_id)
        if request is None:
            return
        self._release_request(runtime, order.order_id, request)

    def _release_request(
        self,
        runtime: ParticipantRuntimeV2,
        order_id: int,
        request: OrderRequest,
    ) -> None:
        if request.purpose == OrderPurpose.BALANCE:
            runtime.balance_reservations.cancel_unfilled(order_id)
        elif request.purpose == OrderPurpose.BATTERY:
            runtime.battery_reservations.cancel_unfilled(order_id)
        elif request.purpose == OrderPurpose.DISPATCHABLE:
            runtime.dispatchable_reservations.cancel_unfilled(order_id)
        elif request.purpose == OrderPurpose.FLEX_LOAD:
            runtime.flex_load_reservations.cancel_unfilled(order_id)
        runtime.reserved_cash_by_order.pop(order_id, None)

    def _consume_cash_reservation(
        self,
        runtime: ParticipantRuntimeV2,
        order_id: int,
        request: OrderRequest,
        fill_qty: Decimal,
    ) -> None:
        reserved = runtime.reserved_cash_by_order.get(order_id, Decimal("0"))
        unit_price = abs(request.price)
        consumed = usd_for_energy(unit_price, fill_qty)
        remaining = max(Decimal("0"), reserved - consumed)
        if remaining:
            runtime.reserved_cash_by_order[order_id] = remaining
        else:
            runtime.reserved_cash_by_order.pop(order_id, None)

    @staticmethod
    def _worst_case_cash_debit(request: OrderRequest) -> Decimal:
        if request.side == "buy" and request.price > 0:
            return usd_for_energy(request.price, request.qty_kwh)
        if request.side == "sell" and request.price < 0:
            return usd_for_energy(-request.price, request.qty_kwh)
        return Decimal("0")

    def _drop_interval_orders(self, participant_id: int, interval_id: str) -> None:
        for oid, owner in list(self._owners.items()):
            request = self._requests[oid]
            if owner == participant_id and request.interval.interval_id == interval_id:
                self._owners.pop(oid, None)
                self._requests.pop(oid, None)

    def _participant(self, participant_id: int) -> ParticipantRuntimeV2:
        try:
            return self.participants[participant_id]
        except KeyError as exc:
            raise KeyError(f"participant {participant_id} is not registered") from exc
