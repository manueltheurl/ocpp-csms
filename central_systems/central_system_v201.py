
# SPDX-License-Identifier: Apache-2.0
# Copyright 2020 - 2024 Pionix GmbH and Contributors to EVerest
import asyncio
import logging
from datetime import datetime, timezone
import sys

from exi_generator import EXIGenerator

import kvas

from ocpp.routing import on
from ocpp.v201 import ChargePoint as cp
from ocpp.v201 import call, call_result
from ocpp.v201.datatypes import IdTokenInfoType, SetVariableDataType, GetVariableDataType, ComponentType, VariableType
from ocpp.v201.enums import (
    Action,
    RegistrationStatusEnumType,
    AuthorizationStatusEnumType,
    AttributeEnumType,
    NotifyEVChargingNeedsStatusEnumType,
    GenericStatusEnumType,
    Iso15118EVCertificateStatusEnumType
)

logging.basicConfig(level=logging.INFO)


class ChargePoint201(cp):
    # Class-level callback for meter values
    meter_value_callback = None
    # Class-level callback for heartbeat
    heartbeat_callback = None
    # Class-level callback for ping
    ping_callback = None
    # Class-level callback for status notification
    status_notification_callback = None
    # Class-level callbacks for connection status
    connection_established_callback = None
    connection_closed_callback = None
    # Class-level callback for (any) DataTransfer and TransactionEvent - declared here
    # (not just assigned onto the class from app.py) and actually invoked below.
    # Previously app.py registered these but nothing in this class ever called them,
    # so no DataTransfer reached the GUI in 2.0.1 mode - not K-VAS, not
    # AcDetected/TriggeringOnPowerOutage either (see the K-VAS plan's B3).
    data_transfer_callback = None
    transaction_callback = None
    # Class-level callback for decoded K-VAS records / key-issue events (GUI).
    kvas_callback = None

    def __init__(self, *args, iso15118_certs, **kwargs):
        super().__init__(*args, **kwargs)
        self.iso15118_certs = iso15118_certs
        if iso15118_certs:
            self.exi_generator = EXIGenerator(
                certs_path=self.iso15118_certs.as_posix())
        else:
            self.exi_generator = None
        self._periodic_task = None

    async def start(self):
        """Start the charge point and periodic metering task."""
        # Notify connection established
        if ChargePoint201.connection_established_callback:
            ChargePoint201.connection_established_callback(self.id)
        
        # NOTE: Periodic TriggerMessage disabled - device sends MeterValues automatically
        # The charge point is configured to send meter values every 3 seconds automatically
        # via MeterValueSampleInterval configuration. Sending TriggerMessage causes
        # connection issues as MicroOcpp library rejects them.
        
        try:
            await super().start()
        finally:
            # Notify connection closed when start() completes (client disconnected)
            if ChargePoint201.connection_closed_callback:
                ChargePoint201.connection_closed_callback(self.id)

    async def _periodic_meter_value_request(self):
        """DISABLED: Device sends MeterValues automatically based on MeterValueSampleInterval config."""
        # Not used - charge point sends meter values autonomously
        pass

    @on(Action.boot_notification)
    def on_boot_notification(self, **kwargs):
        logging.debug("Received a BootNotification")
        return call_result.BootNotification(current_time=datetime.now(timezone.utc).isoformat(),
                                                   interval=300, status=RegistrationStatusEnumType.accepted)

    @on(Action.status_notification)
    def on_status_notification(self, **kwargs):
        try:
            evse_id = kwargs.get('evse_id', 0)
            connector_id = kwargs.get('connector_id', 0)
            status = kwargs.get('connector_status', 'Unknown')
            
            logging.info(f"📡 StatusNotification - EVSE {evse_id}, Connector {connector_id}: {status}")
            
            # Trigger callback if registered
            if ChargePoint201.status_notification_callback:
                ChargePoint201.status_notification_callback({
                    'evse_id': evse_id,
                    'connector_id': connector_id,
                    'status': status,
                    'raw_data': kwargs
                })
        except Exception as e:
            logging.error(f"❌ Error processing StatusNotification: {e}")
        
        return call_result.StatusNotification()

    @on(Action.heartbeat)
    def on_heartbeat(self, **kwargs):
        # Call the callback if registered
        if ChargePoint201.heartbeat_callback:
            ChargePoint201.heartbeat_callback()
        return call_result.Heartbeat(current_time=datetime.now(timezone.utc).isoformat())

    @on(Action.authorize)
    def on_authorize(self, **kwargs):
        return call_result.Authorize(
            id_token_info=IdTokenInfoType(
                status=AuthorizationStatusEnumType.accepted
            )
        )

    @on(Action.notify_report)
    def on_notify_report(self, **kwargs):
        return call_result.NotifyReport()

    @on(Action.log_status_notification)
    def on_log_status_notification(self, **kwargs):
        return call_result.LogStatusNotification()

    @on(Action.firmware_status_notification)
    def on_firmware_status_notification(self, **kwargs):
        return call_result.FirmwareStatusNotification()

    @on(Action.transaction_event)
    def on_transaction_event(self, **kwargs):
        try:
            # Log RAW TransactionEvent for debugging
            logging.info(f"🔍 RAW TransactionEvent received: {kwargs}")
            
            event_type = kwargs.get('event_type', 'unknown')
            transaction_info = kwargs.get('transaction_info', {})
            transaction_id = transaction_info.get('transaction_id', 'unknown')
            evse_id = kwargs.get('evse', {}).get('id', 'unknown')
            
            logging.info(f"📊 TransactionEvent: type={event_type}, transaction_id={transaction_id}, evse_id={evse_id}")

            # B3 fix: this was never wired to transaction_callback in 2.0.1 mode (see
            # data_transfer_callback above for the same class of bug) - app.js's
            # transaction start/stop indicators never lit up under OCPP 2.0.1.
            if ChargePoint201.transaction_callback:
                if event_type == 'Started':
                    ChargePoint201.transaction_callback({'event': 'start', 'transaction_id': transaction_id})
                elif event_type == 'Ended':
                    ChargePoint201.transaction_callback({'event': 'stop', 'transaction_id': transaction_id})

            # Extract meter values from the transaction event
            meter_value = kwargs.get('meter_value', [])
            
            energy_value = None
            voltage_value = None
            soc_value = None
            
            if meter_value:
                logging.info(f"   📈 Processing {len(meter_value)} meter value(s) in TransactionEvent:")
                for mv in meter_value:
                    timestamp = mv.get('timestamp', 'unknown')
                    sampled_values = mv.get('sampled_value', [])
                    logging.info(f"      Timestamp: {timestamp}")
                    for sv in sampled_values:
                        value = sv.get('value', 'N/A')
                        measurand = sv.get('measurand', 'N/A')
                        unit_of_measure = sv.get('unit_of_measure', {})
                        unit = unit_of_measure.get('unit', 'N/A') if unit_of_measure else 'N/A'
                        location = sv.get('location', 'N/A')
                        phase = sv.get('phase', 'N/A')
                        logging.info(f"         {measurand}: {value} {unit} (Location: {location}, Phase: {phase})")
                        
                        if measurand == 'Energy.Active.Import.Register':
                            energy_value = value
                            logging.info(f"         ⚡ Energy: {value} {unit}")
                        elif measurand == 'Voltage':
                            voltage_value = value
                            logging.info(f"         ⚡ Voltage: {value} {unit}")
                        elif measurand == 'SoC':
                            soc_value = value
                            logging.info(f"         🔋 SoC: {value} {unit}")
                
                # Call the callback if we have meter values
                if ChargePoint201.meter_value_callback and (energy_value or voltage_value or soc_value):
                    logging.info(f"   📤 Triggering meter_value_callback - Energy: {energy_value}, Voltage: {voltage_value}, SoC: {soc_value}")
                    ChargePoint201.meter_value_callback({
                        'evse_id': evse_id,
                        'energy': energy_value,
                        'voltage': voltage_value,
                        'soc': soc_value,
                        'raw_data': kwargs
                    })
            
            return call_result.TransactionEvent()
        except Exception as e:
            logging.error(f"❌ Error processing TransactionEvent: {e}")
            import traceback
            logging.error(traceback.format_exc())
            return call_result.TransactionEvent()

    @on(Action.meter_values)
    def on_meter_values(self, **kwargs):
        try:
            # Log the RAW received data for debugging
            logging.info(f"🔍 RAW MeterValues received: {kwargs}")
            
            # Log the received meter values for debugging
            evse_id = kwargs.get('evse_id', 'unknown')
            meter_value = kwargs.get('meter_value', [])
            
            energy_value = None
            voltage_value = None
            soc_value = None
            temperatures = {
                'L1': None,
                'L2': None,
                'L3': None,
                'N': None
            }
            
            if not meter_value:
                logging.warning(f"⚠️  Empty MeterValues received for EVSE {evse_id}")
            else:
                logging.info(f"✅ MeterValues received for EVSE {evse_id}:")
                for mv in meter_value:
                    timestamp = mv.get('timestamp', 'unknown')
                    sampled_values = mv.get('sampled_value', [])
                    logging.info(f"   Timestamp: {timestamp}")
                    for sv in sampled_values:
                        value = sv.get('value', 'N/A')
                        measurand = sv.get('measurand', 'N/A')
                        unit_of_measure = sv.get('unit_of_measure', {})
                        unit = unit_of_measure.get('unit', 'N/A') if unit_of_measure else 'N/A'
                        location = sv.get('location', 'N/A')
                        phase = sv.get('phase', 'N/A')
                        logging.info(f"     {measurand}: {value} {unit} (Location: {location}, Phase: {phase})")
                        
                        if measurand == 'Energy.Active.Import.Register':
                            energy_value = value
                        elif measurand == 'Voltage':
                            voltage_value = value
                            logging.info(f"     ⚡ Voltage: {value} {unit}")
                        elif measurand == 'SoC':
                            soc_value = value
                            logging.info(f"     🔋 SoC: {value} {unit}")
                        
                        # Extract temperature values - handle both formats:
                        # Format 1: measurand="Temperature", phase="L1"
                        # Format 2: measurand="Temperature.Outlet.L1", location="Outlet"
                        if measurand.startswith('Temperature'):
                            try:
                                temp_value = float(value)
                                
                                # Check phase field first (OCPP 2.0.1 style)
                                if phase == 'L1':
                                    temperatures['L1'] = temp_value
                                    logging.info(f"     🌡️  Temperature L1: {temp_value}°C")
                                elif phase == 'L2':
                                    temperatures['L2'] = temp_value
                                    logging.info(f"     🌡️  Temperature L2: {temp_value}°C")
                                elif phase == 'L3':
                                    temperatures['L3'] = temp_value
                                    logging.info(f"     🌡️  Temperature L3: {temp_value}°C")
                                elif phase == 'N':
                                    temperatures['N'] = temp_value
                                    logging.info(f"     🌡️  Temperature N: {temp_value}°C")
                                # Check measurand string (MicroOcpp style: Temperature.Outlet.L1)
                                elif '.L1' in measurand:
                                    temperatures['L1'] = temp_value
                                    logging.info(f"     🌡️  Temperature L1: {temp_value}°C")
                                elif '.L2' in measurand:
                                    temperatures['L2'] = temp_value
                                    logging.info(f"     🌡️  Temperature L2: {temp_value}°C")
                                elif '.L3' in measurand:
                                    temperatures['L3'] = temp_value
                                    logging.info(f"     🌡️  Temperature L3: {temp_value}°C")
                                elif '.N' in measurand:
                                    temperatures['N'] = temp_value
                                    logging.info(f"     🌡️  Temperature N: {temp_value}°C")
                            except (ValueError, TypeError) as e:
                                logging.warning(f"Could not parse temperature value: {value}")

            # Call the callback if registered
            if ChargePoint201.meter_value_callback:
                ChargePoint201.meter_value_callback({
                    'evse_id': evse_id,
                    'energy': energy_value,
                    'voltage': voltage_value,
                    'soc': soc_value,
                    'temperatures': temperatures,
                    'raw_data': kwargs
                })
            
            return call_result.MeterValues()
        except Exception as e:
            logging.error(f"❌ Error processing MeterValues: {e}")
            import traceback
            logging.error(traceback.format_exc())
            return call_result.MeterValues()

    @on(Action.notify_charging_limit)
    def on_notify_charging_limit(self, **kwargs):
        return call_result.NotifyChargingLimit()

    @on(Action.notify_customer_information)
    def on_notify_customer_information(self, **kwargs):
        return call_result.NotifyCustomerInformation()

    @on(Action.notify_ev_charging_needs)
    def on_notify_ev_charging_needs(self, **kwargs):
        return call_result.NotifyEVChargingNeeds(status=NotifyEVChargingNeedsStatusEnumType.accepted)

    @on(Action.notify_ev_charging_schedule)
    def on_notify_ev_charging_schedule(self, **kwargs):
        return call_result.NotifyEVChargingSchedule(status=GenericStatusEnumType.accepted)

    @on(Action.notify_event)
    def on_notify_event(self, **kwargs):
        return call_result.NotifyEvent()

    @on(Action.notify_monitoring_report)
    def on_notify_monitoring_report(self, **kwargs):
        return call_result.NotifyMonitoringReport()

    @on(Action.publish_firmware_status_notification)
    def on_publish_firmware_status_notification(self, **kwargs):
        return call_result.PublishFirmwareStatusNotification()

    @on(Action.report_charging_profiles)
    def on_report_charging_profiles(self, **kwargs):
        return call_result.ReportChargingProfiles()

    @on(Action.reservation_status_update)
    def on_reservation_status_update(self, **kwargs):
        return call_result.ReservationStatusUpdate()

    @on(Action.security_event_notification)
    def on_security_event_notification(self, **kwargs):
        return call_result.SecurityEventNotification()

    @on(Action.sign_certificate)
    def on_sign_certificate(self, **kwargs):
        return call_result.SignCertificate(status=GenericStatusEnumType.accepted)

    @on(Action.get_15118_ev_certificate)
    def on_get_15118_ev_certificate(self, **kwargs):
        if not self.exi_generator:
            return call.create_call_error(f'iso15118 certificate path "{self.iso15118_certs.as_posix()}" not found')
        exi_request = kwargs["exi_request"]
        namespace = kwargs['iso15118_schema_version']
        return call_result.Get15118EVCertificate(
            status=Iso15118EVCertificateStatusEnumType.accepted,
            exi_response=self.exi_generator.generate_certificate_installation_res(
                exi_request,
                namespace
            )
        )

    @on(Action.get_certificate_status)
    def on_get_certificate_status(self, **kwargs):
        return call_result.GetCertificateStatus(status=GenericStatusEnumType.accepted,
                                                       ocsp_result="IS_FAKED")

    @on(Action.data_transfer)
    async def on_data_transfer(self, **kwargs):
        vendor_id = kwargs.get('vendor_id', 'Unknown')
        message_id = kwargs.get('message_id', 'Unknown')
        data = kwargs.get('data', {})

        logging.info(f"📦 DataTransfer received - VendorId: {vendor_id}, MessageId: {message_id}")

        # K-VAS (kr.or.keco): GetEncKey and Battery Info. Answered SYNCHRONOUSLY from
        # the OCPP protocol's point of view - response_data below becomes
        # DataTransferResponse.data directly, as an OBJECT (never a bare string - see
        # TODO.md's "CSMS returns data=\"\"" note in the MCU repo for why that
        # distinction matters to the MCU's JSON parser) - but kvas.handle() is itself
        # `async def` because KVAS_MODE=relay needs to await a
        # loop.run_in_executor() HTTP round trip to KECO without blocking every other
        # charger's OCPP traffic on this event loop (plan D3). local_ministry mode
        # does no I/O and returns immediately either way - this handler being async
        # doesn't change its behaviour, python-ocpp awaits both sync and async
        # `@on` handlers transparently (see ocpp.charge_point._handle_call).
        if vendor_id == kvas.VENDOR_ID:
            try:
                response_data, gui_event = await kvas.handle(vendor_id, message_id, data)
            except Exception as e:
                logging.error(f"❌ K-VAS handler error ({message_id}): {e}", exc_info=True)
                response_data = {"resultCode": "0"} if message_id == "Battery Info" else {}
                gui_event = None
            if gui_event and ChargePoint201.kvas_callback:
                try:
                    ChargePoint201.kvas_callback(gui_event)
                except Exception as e:
                    logging.error(f"❌ Error in kvas_callback: {e}")
            return call_result.DataTransfer(status=GenericStatusEnumType.accepted,
                                             data=response_data)

        try:
            # Handle temperature data (kept as its own path - already routes through
            # meter_value_callback and is known-working)
            if vendor_id == 'SmartyPlugger' and message_id == 'TemperatureData':
                logging.info(f"🌡️  Temperature Data:")

                temperatures = {
                    'L1': data.get('l1'),
                    'L2': data.get('l2'),
                    'L3': data.get('l3'),
                    'N': data.get('n')
                }

                for phase, temp in temperatures.items():
                    if temp is not None:
                        logging.info(f"      Phase {phase}: {temp}°C")

                # Trigger meter value callback if registered (for GUI display)
                if ChargePoint201.meter_value_callback:
                    # Format temperature data to match meter value callback expectations
                    ChargePoint201.meter_value_callback({
                        'temperatures': temperatures,
                        'source': 'DataTransfer'
                    })
            else:
                logging.info(f"   Data: {data}")

            # Generic path (B3 fix): relay every DataTransfer to the registered
            # callback so the GUI can react to it, same as the 1.6 class already
            # does. This is what makes AcDetected/TriggeringOnPowerOutage (and any
            # future vendor message) reach the GUI in 2.0.1 mode.
            if ChargePoint201.data_transfer_callback:
                ChargePoint201.data_transfer_callback({
                    'vendor_id': vendor_id,
                    'message_id': message_id,
                    'data': data,
                })
        except Exception as e:
            logging.error(f"❌ Error processing DataTransfer: {e}")

        return call_result.DataTransfer(status=GenericStatusEnumType.accepted, data="")

    async def set_variables_req(self, **kwargs):
        payload = call.SetVariables(**kwargs)
        return await self.call(payload)

    async def set_config_variables_req(self, component_name, variable_name, value):
        el = SetVariableDataType(
            attribute_value=value,
            attribute_type=AttributeEnumType.actual,
            component=ComponentType(
                name=component_name
            ),
            variable=VariableType(
                name=variable_name
            )
        )
        payload = call.SetVariables([el])
        return await self.call(payload)

    async def get_variables_req(self, **kwargs):
        payload = call.GetVariables(**kwargs)
        return await self.call(payload)

    async def get_config_variables_req(self, component_name, variable_name):
        el = GetVariableDataType(
            component=ComponentType(
                name=component_name
            ),
            variable=VariableType(
                name=variable_name
            ),
            attribute_type=AttributeEnumType.actual
        )
        payload = call.GetVariables([el])
        return await self.call(payload)

    async def get_base_report_req(self, **kwargs):
        payload = call.GetBaseReport(**kwargs)
        return await self.call(payload)

    async def get_report_req(self, **kwargs):
        payload = call.GetReport(**kwargs)
        return await self.call(payload)

    async def reset_req(self, **kwargs):
        payload = call.Reset(**kwargs)
        return await self.call(payload)

    async def request_start_transaction_req(self, **kwargs):
        payload = call.RequestStartTransaction(**kwargs)
        return await self.call(payload)

    async def request_stop_transaction_req(self, **kwargs):
        payload = call.RequestStopTransaction(**kwargs)
        return await self.call(payload)

    async def change_availablility_req(self, **kwargs):
        payload = call.ChangeAvailability(**kwargs)
        return await self.call(payload)

    async def clear_cache_req(self, **kwargs):
        payload = call.ClearCache(**kwargs)
        return await self.call(payload)

    async def cancel_reservation_req(self, **kwargs):
        payload = call.CancelReservation(**kwargs)
        return await self.call(payload)

    async def certificate_signed_req(self, **kwargs):
        payload = call.CertificateSigned(**kwargs)
        return await self.call(payload)

    async def clear_charging_profile_req(self, **kwargs):
        payload = call.ClearChargingProfile(**kwargs)
        return await self.call(payload)

    async def clear_display_message_req(self, **kwargs):
        payload = call.ClearDisplayMessage(**kwargs)
        return await self.call(payload)

    async def clear_charging_limit_req(self, **kwargs):
        payload = call.ClearedChargingLimit(**kwargs)
        return await self.call(payload)

    async def clear_variable_monitoring_req(self, **kwargs):
        payload = call.ClearVariableMonitoringd(**kwargs)
        return await self.call(payload)

    async def cost_update_req(self, **kwargs):
        payload = call.CostUpdated(**kwargs)
        return await self.call(payload)

    async def customer_information_req(self, **kwargs):
        payload = call.CustomerInformation(**kwargs)
        return await self.call(payload)

    async def data_transfer_req(self, **kwargs):
        payload = call.DataTransfer(**kwargs)
        return await self.call(payload)

    async def delete_certificate_req(self, **kwargs):
        payload = call.DeleteCertificate(**kwargs)
        return await self.call(payload)

    async def get_charging_profiles_req(self, **kwargs):
        payload = call.GetChargingProfiles(**kwargs)
        return await self.call(payload)

    async def get_composite_schedule_req(self, **kwargs):
        payload = call.GetCompositeSchedule(**kwargs)
        return await self.call(payload)

    async def get_display_nessages_req(self, **kwargs):
        payload = call.GetDisplayMessages(**kwargs)
        return await self.call(payload)

    async def get_installed_certificate_ids_req(self, **kwargs):
        payload = call.GetInstalledCertificateIds(**kwargs)
        return await self.call(payload)

    async def get_local_list_version(self, **kwargs):
        payload = call.GetLocalListVersion(**kwargs)
        return await self.call(payload)

    async def get_log_req(self, **kwargs):
        payload = call.GetLog(**kwargs)
        return await self.call(payload)

    async def get_transaction_status_req(self, **kwargs):
        payload = call.GetTransactionStatus(**kwargs)
        return await self.call(payload)

    async def install_certificate_req(self, **kwargs):
        payload = call.InstallCertificate(**kwargs)
        return await self.call(payload)

    async def publish_firmware_req(self, **kwargs):
        payload = call.PublishFirmware(**kwargs)
        return await self.call(payload)

    async def reserve_now_req(self, **kwargs):
        payload = call.ReserveNow(**kwargs)
        return await self.call(payload)

    async def send_local_list_req(self, **kwargs):
        payload = call.SendLocalList(**kwargs)
        return await self.call(payload)

    async def set_charging_profile_req(self, **kwargs):
        payload = call.SetChargingProfile(**kwargs)
        return await self.call(payload)

    async def set_display_message_req(self, **kwargs):
        payload = call.SetDisplayMessage(**kwargs)
        return await self.call(payload)

    async def set_monitoring_base_req(self, **kwargs):
        payload = call.SetMonitoringBase(**kwargs)
        return await self.call(payload)

    async def set_monitoring_level_req(self, **kwargs):
        payload = call.SetMonitoringLevel(**kwargs)
        return await self.call(payload)

    async def set_network_profile_req(self, **kwargs):
        payload = call.SetNetworkProfile(**kwargs)
        return await self.call(payload)

    async def set_variable_monitoring_req(self, **kwargs):
        payload = call.SetVariableMonitoring(**kwargs)
        return await self.call(payload)

    async def trigger_message_req(self, **kwargs):
        payload = call.TriggerMessage(**kwargs)
        return await self.call(payload)

    async def unlock_connector_req(self, **kwargs):
        payload = call.UnlockConnector(**kwargs)
        return await self.call(payload)

    async def unpublish_firmware_req(self, **kwargs):
        payload = call.UnpublishFirmware(**kwargs)
        return await self.call(payload)

    async def update_firmware(self, **kwargs):
        payload = call.UpdateFirmware(**kwargs)
        return await self.call(payload)
