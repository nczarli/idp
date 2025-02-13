"""Robot_controller controller."""
import logging
import math

import controller
import numpy as np

from calculations import *
from reroute import reroute_after_collision_prevention
from field import Field
from instrumentation import trace
from robot import Robot

np.set_printoptions(suppress=True)

DEBUG_PID = False
DEBUG_TRACING = False
DEBUG_TRANSLATE = False
DEBUG_MAINLOOP = False
Robot.DEBUG_COLLISIONS = False
BOX_AVOIDANCE = False


# Default level is WARNING, change it to DEBUG
logging.basicConfig(level=logging.DEBUG)

if not DEBUG_TRACING:
    logging.getLogger('tracing').setLevel(logging.INFO)


# Initialize robot
r = controller.Robot()
if r.getName() == 'robot_red':
    robot = Robot(r, 'red')

else:
    robot = Robot(r, 'green')

red_field = Field('red')
green_field = Field('green')


@trace
def encircle(coord, location, field):
    """
    location is 3D gps coordinates
    coord is 2D target coordinate

    this assumes both coord and location are in the same half of the field
    """
    if not robot.field_collision(coord, field):
        pass
    else:
        checkpoint, bearing = robot.find_closest_point(field)
        move(checkpoint, error_translation=0.1)

        speed_inner_wheel = 2
        speed_outer_wheel = 3.5

        # determine whether its better to turn anticlockwise
        clockwise = turn_clockwise(coord, location, field)

        if clockwise:
            bearing += 90
            if bearing > 180:
                bearing -= 360
        else:
            bearing -= 90
            if bearing <= -180:
                bearing += 360

        PID_rotation(bearing)

        collision = robot.field_collision(coord, field)
        while collision:

            if clockwise:
                robot.set_motor_velocities(speed_inner_wheel, speed_outer_wheel)
            else:
                robot.set_motor_velocities(speed_outer_wheel, speed_inner_wheel)
            robot.step()
            collision = robot.field_collision(coord, field)

    robot.reset_motor_velocities()


@trace
@reroute_after_collision_prevention(robot)
def move_avoid_fields(coord, error_translation=0.1):
    """
    avoids both fields
    """
    location = robot.gps.getValues()

    if location[2] * coord[1] >= 0:  # target and current location are on the same half

        if location[2] > 0:
            # red half
            encircle(coord, location, red_field)
        else:
            # green half
            encircle(coord, location, green_field)
        PID_translation(coord, error_translation)

    else:  # opposite halfs
        if coord[0] > 0.5 or coord[0] < -0.5:
            intermediate = [coord[0], 0]
        else:
            if coord[0] > 0:
                intermediate = [0.5, 0]
            else:
                intermediate = [-0.5, 0]
        if location[2] > 0:  # red half
            intermediate[1] = -0.2
            encircle(intermediate, location, red_field)
            PID_translation(intermediate, error_translation)

            encircle(coord, robot.gps.getValues(), green_field)
            PID_translation(coord, error_translation)
        else:  # green half
            intermediate[1] = 0.2
            encircle(intermediate, location, green_field)
            PID_translation(intermediate, error_translation)

            encircle(coord, robot.gps.getValues(), red_field)
            PID_translation(coord, error_translation)

    return


# @trace
def PID_rotation(required, threshold=0.4) -> bool:
    """Rotate until the required bearing is reached.
    Exits if error < threshold or oscillatory behaviour is detected.

    Args:
        required (float): Bearing in degrees.
        threshold (float, optional): Maximum acceptable error. Defaults to 0.4.
    Returns:
        bool: True if error < threshold
    """
    kP = 11.0
    kI = 0.90
    kD = 7.55

    def angle_between(a, b):
        return min(a - b, a - b + 360, a - b - 360, key=abs)

    error = angle_between(required, robot.bearing1(robot.compass))
    error_integral = 0
    error_derivative = 0

    while abs(error) > threshold:
        P = kP * error
        I = kI * error_integral
        D = kD * error_derivative

        v = np.clip(P + I + D, -robot.MAX_VELOCITY, robot.MAX_VELOCITY)

        # if DEBUG_PID:
        # print(f'{P=}, {I=}, {D=}, {v=}, {error=}, {error_integral=}, {error_derivative=}')

        robot.set_motor_velocities(-v, v)

        time_elapsed = robot.step()

        new_error = angle_between(required, robot.bearing1(robot.compass))

        # If more than one TIME_STEPs elapsed, then the robot had stopped due to collision prevention
        # Reset to avoid blowing up
        if np.isclose(time_elapsed, robot.TIME_STEP / 1000, atol=0.001):
            error_integral += new_error * time_elapsed
            error_derivative = (new_error - error) / time_elapsed
        else:
            if DEBUG_PID:
                print('PID_rotation resetting state due to collision detection')
            error_integral = 0
            error_derivative = 0

        # Detect oscillatory behaviour
        # On a 64 ms TIME_STEP, it sometimes oscillates between 0.39 and -0.39 deg error
        if np.isclose(error, -new_error, atol=0.1):
            if DEBUG_PID:
                print('PID_rotation halted due to oscillations')
            return False

        error = new_error

    robot.reset_motor_velocities()

    return True


@trace
def PID_translation(coord, final_error=0.15, reverse=False):
    """input: 2D desired coordinate coord,
    The function moves in a straight line until the desired location is within
    the final error distance"""
    coord = np.clip(coord, -1, 1)

    # Euclidean distance
    error = np.linalg.norm(coord - robot.current_location())
    error_integral = 0
    error_derivative = 0

    kP = 10.0
    kI = 0.9
    kD = 2.0

    time_since_rotation = np.inf
    while error > final_error:
        # Correct bearing (returns immediately if no correction is required)
        if time_since_rotation > 0.2:
            bearing = required_bearing(coord, robot.gps.getValues())
            if reverse:
                if bearing > 0:
                    bearing -= 180
                else:
                    bearing += 180

            PID_rotation(bearing, 5)
            time_since_rotation = 0

        P = kP * error
        I = kI * error_integral
        D = kD * error_derivative
        v = np.clip(P+I+D, -robot.MAX_VELOCITY, robot.MAX_VELOCITY)

        if reverse:
            v *= -1

        robot.set_motor_velocities(v, v)

        time_elapsed = robot.step()
        time_since_rotation += time_elapsed

        new_error = np.linalg.norm(coord - robot.current_location())

        if np.isclose(time_elapsed, robot.TIME_STEP / 1000, atol=0.001):
            error_integral += new_error * time_elapsed
            error_derivative = (new_error - error) / time_elapsed
        else:
            if DEBUG_TRANSLATE:
                print('PID_translation resetting state due to collision detection')
            error_integral = 0
            error_derivative = 0

        error = new_error

        if BOX_AVOIDANCE:
            box_collision(coord, threshold_distance=0.45)

    robot.reset_motor_velocities()


@trace
@reroute_after_collision_prevention(robot)
def move(coord, error_translation=0.1):
    """
    Straight line move
    """
    PID_translation(coord, error_translation)


@trace
def sweep(velocity=1.8, swept_angle=355):
    """
    do a 180 degree spin while collecting data from distance sensor
    input: velocity of wheels/how fast is the rotation
    output: numpy array with stored values from the distance sensor
    """

    # find current rotation [0-360 degrees]
    initial_angle = robot.bearing(robot.compass1)

    # store potential boxes locations
    boxes = []

    # sweep 360 degrees
    swept_angle = 0

    # wait for infrared sensor to initialize
    # worst-case scenario: 48 ms for a measurement + 5 ms to put the output on the bus
    while robot._robot.getTime() < (48 + 5) / 1000:
        robot.step(read_sensors=False)

    while swept_angle < 355:
        robot.set_motor_velocities(-velocity, velocity)

        robot.step(read_sensors=False)

        # distance from robot centre to wall in this direction
        current_angle = robot.bearing(robot.compass1)
        current_position = robot.gps.getValues()
        wall_dist = get_wall_position(current_angle, current_position)

        # wall_dist is decreased by robot-sensor distance
        wall_dist -= 0.11

        # get quantized infrared level and convert to volts
        infrared_volts = robot.infrared_analogue.read() * robot.infrared_vref / 1023
        # get infrared reading and convert to meters
        infrared_dist = 0.7611 * math.pow(infrared_volts, -0.9313) - 0.1252

        # print(infrared_dist, wall_dist)

        # if measured distance is less than wall_dist then assume there's a box
        # also if wall is more than 1.5 away disregard measurements because it's further than sensor's range
        if abs(wall_dist - infrared_dist) > 0.09 and wall_dist < 1.4:
            valid, x, z = potential_box_position(infrared_dist + 0.11, current_angle, current_position)
            if(valid):
                boxes.append([x, z])

        # check if boxes are in between the robots
        if abs(wall_dist - infrared_dist) > 0.1 and wall_dist > 1.4 and abs(infrared_dist) < 0.4:
            valid, x, z = potential_box_position(infrared_dist + 0.11, current_angle, current_position)
            if(valid):
                boxes.append([x, z])
                # print(x)

        if current_angle > initial_angle:
            swept_angle = current_angle - initial_angle
        else:
            swept_angle = 360 - initial_angle + current_angle

    locations = box_position(np.array(boxes))
    # print(locations)

    robot.reset_motor_velocities()
    robot.step(read_sensors=False)

    robot.sweep_locations = locations

    return locations


@trace
def second_sweep(velocity=-0.5, swept_angle=355):
    """
    360 sweep to find other boxes which are hidden
    input: velocity of wheels/how fast is the rotation
    output: numpy array with stored values from the distance sensor
            -1 if there are no blocks left to be picked up
    """

    robot.step()

    if(robot.colour == 'red'):
        move([0.0161, 1])
        move([0.4, 1])
        move([0.4, 0])
        PID_rotation(0)
    else:
        move([0.0161, -1])
        move([-0.4, -1])
        move([-0.4, 0])
        PID_rotation(180)

    robot.send_message('sweep ready', 9)
    robot.sweep_ready = True

    # wait until both robots are in the right position
    while not robot.other_sweep_ready:
        robot.step()
    # find current rotation [0-360 degrees]
    initial_angle = robot.bearing(robot.compass1)

    # store potential boxes locations
    boxes = []

    # sweep 360 degrees
    swept_angle = 0

    # wait for infrared sensor to initialize
    # worst-case scenario: 48 ms for a measurement + 5 ms to put the output on the bus
    while robot._robot.getTime() < (48 + 5) / 1000:
        robot.step()

    while swept_angle < 355:
        robot.set_motor_velocities(-velocity, velocity)

        robot.step()
        # distance from robot centre to wall in this direction
        current_angle = robot.bearing(robot.compass1)
        current_position = robot.gps.getValues()
        wall_dist = get_wall_position(current_angle, current_position)

        # wall_dist is decreased by robot-sensor distance
        wall_dist -= 0.11

        # get quantized infrared level and convert to volts
        infrared_volts = robot.infrared_analogue.read() * robot.infrared_vref / 1023
        # get infrared reading and convert to meters
        infrared_dist = 0.7611 * math.pow(infrared_volts, -0.9313) - 0.1252

        # print(infrared_dist, wall_dist)

        # puts all measurements into potential_box_position and filters depending on whether box is
        # inside the boxes
        if(abs(wall_dist - infrared_dist) > 0.1 and (wall_dist < 1.4)):
            valid, x, z = potential_box_position(infrared_dist + 0.11, current_angle, current_position)
            # field_boxes_green = [[0.2, -0.205], [0.2,-0.595], [-0.2,-0.595], [-0.2, -0.195]]
            # field_boxes_red   = [[0.2, 0.205] , [0.2, 0.595], [-0.2, 0.595]. [-0.2, 0.195]]
            # field_red_robot   = [[0.12, 0.275], [0.12, -0.275],  [0.66, -0.275], [0.66, 0.275]]
            # field_greed_robot = [[-0.12, 0.275], [-0.12, -0.275], [-0.66, -0.275], [-0.66, 0.275]]

            avoid = 0
            # ignore boxes that are in the fields
            if (-0.2 < x < 0.2) and (-0.595 < z < -0.195):
                avoid = 1
            elif (-0.2 < x < 0.2) and (0.195 < z < 0.595):
                avoid = 1
            # ignore robots
            elif (-0.3 < z < 0.3) and (0.1 < x < 0.7):
                avoid = 1
            elif(-0.3 < z < 0.3) and (-0.7 < x < -0.1):
                avoid = 1

            elif valid and avoid == 0:
                boxes.append([x, z])

        if current_angle > initial_angle:
            swept_angle = current_angle - initial_angle
        else:
            swept_angle = 360 - initial_angle + current_angle

    # try/except since other functions will give errors if no blocks
    # have been found
    try:
        locations = box_position(np.array(boxes))
        robot.reset_motor_velocities()
        robot.step()
    except:
        locations = np.array([])

    robot.sweep_locations = locations
    return locations


@trace
@reroute_after_collision_prevention(robot)
def return_box_field(coord):
    """
    function that makes robot return a box in the specified field without it clashing with
    other boxes that are already in the field
    input 3D coordinates of the robot
    """
    robot.carrying = True
    intermediate, final = robot.field.get_to_field(coord)
    move_avoid_fields(intermediate, error_translation=0.15, reroute=False)
    if final[0] > 0:
        PID_rotation(-90)
    else:
        PID_rotation(90)

    PID_translation(final, 0.2)

    robot.withdraw_dualclaw()

    robot.move_forwards(-0.15, 0.02)
    robot.carrying = False
    return


@trace
@reroute_after_collision_prevention(robot)
def finish_in_field():
    """
    for the ending of the task, robot goes and stays in its field
    """
    if robot.colour == 'red':
        intermediate = (0, 1)
        final = (0, 0.4)
    else:
        intermediate = (0, -1)
        final = (0, -0.4)

    move_avoid_fields(intermediate, reroute=False)

    # TODO: Think of something better
    if len(robot.box_list) != 0 and robot.field.available():
        return False

    if robot.colour == 'red':
        PID_rotation(180)
    else:
        PID_rotation(0)

    if len(robot.box_list) != 0 and robot.field.available():
        return False

    PID_translation(final, reverse=True)

    return True


def test_collisions_1():
    robot.step()

    if robot.colour == 'green':
        move((0, 1))

    if robot.colour == 'red':
        move((-0, -1))


def test_collisions_2():
    """
    position green to (0, -0.4)
    position red to (0.18, 0.4)
    """
    robot.step()

    if robot.colour == 'green':
        move((0, 1))

    if robot.colour == 'red':
        move((0.18, -1))


def test_collisions_3():
    """
    position red to (-0.4, 0.4)
    leave green as it is
    """
    robot.step()

    if robot.colour == 'green':
        move((0, 1))
    if robot.colour == 'red':
        move((1, 0.4))


def test_collisions_4():
    # FIXME
    robot.step()

    if robot.colour == 'green':
        move((-0.3, -0.4))
        while robot._robot.getTime() < 20:
            robot.step()
        move_avoid_fields((0.3, 0.4))
    if robot.colour == 'red':
        move((0, 0))
        move_avoid_fields((-0.5, 0))


def test_collisions_5():
    # FIXME
    robot.step()

    if robot.colour == 'green':
        move((-0.3, -0.4))
        while robot._robot.getTime() < 20:
            robot.step()
        move_avoid_fields((0.3, 0.5))
    if robot.colour == 'red':
        move((0, 0))
        move_avoid_fields((-0.5, 0))


def test_collisions_6():
    robot.step()

    if robot.colour == 'green':
        move((-0.6, -0.4))
        while robot._robot.getTime() < 10:
            robot.step()
        move_avoid_fields((0.5, 0.8))
    if robot.colour == 'red':
        move((0, 0))
        move_avoid_fields((-0.5, 0))


def test_collisions_7():
    # FIXME
    robot.step()

    if robot.colour == 'green':
        move((-0.6, -0.4))
        while robot._robot.getTime() < 10:
            robot.step()
        move_avoid_fields((0.5, 0.8))
    if robot.colour == 'red':
        move((0, 0))
        while robot._robot.getTime() < 10:
            robot.step()
        move_avoid_fields((-0.5, 0))


def test_collisions_8():
    robot.step()

    if robot.colour == 'green':
        move((-0.6, -0.4))
        while robot._robot.getTime() < 10:
            robot.step()
        move_avoid_fields((0.5, 0.8))
    if robot.colour == 'red':
        move((-0.1, 0))
        while robot._robot.getTime() < 18:
            robot.step()
        move_avoid_fields((-0.5, 0))


def test_move_forwards():
    robot.step()

    assert robot.move_forwards(-0.4)
    for _ in range(10):
        robot.step()
    assert robot.move_forwards(0.1)


def test_move_forwards_2():
    robot.step()

    assert not robot.move_forwards(-2)
    assert robot.move_forwards(0.15)
    assert PID_rotation(20 if robot.colour == 'red' else 160)
    assert not robot.move_forwards(-2)
    assert robot.move_forwards(0.15)


def test_distance_function():
    robot.step()
    robot.step()
    robot.distance_too_small()


@trace
def box_collision(coord, threshold_distance=0.45):
    """
    check for collisions with closest box
    """

    def box_collision_detection(avoidance_box, coord, threshold_distance=0.45):
        check_points = [(0.4, 0.4), (0.4, -0.4), (0, 0), (0.8, 0.0),
                        (-0.4, 0.4), (-0.4, -0.4), (0, 0), (-0.8, 0.0)]
        coord = tuple(coord)
        if coord in check_points:
            # prioritise encirclement over box avoidance
            return False

        location = robot.gps.getValues()
        location = (location[0], location[2])

        m = (coord[1] - location[1])/(coord[0]-location[0])
        c = coord[1] - m*coord[0]

        x = np.linspace(min(coord[0], location[0]), max(coord[0], location[0]), 101, endpoint=True)
        z = m*x + c

        z1 = [i for i in z if (i > avoidance_box[1] - 0.1 and i < avoidance_box[1] + 0.1)]
        x1 = [i for i in x if (i > avoidance_box[0] - 0.1 and i < avoidance_box[0] + 0.1)]

        if z1 and x1:
            print(avoidance_box)
            print(location)
            print(coord, 'coord')
            return True

        return False

    boxes = np.array(Robot.unique_boxes)
    if not list(boxes):
        return
    distances = []
    for box in boxes:
        distance = np.linalg.norm(box - robot.current_location())
        distances.append(distance)

    i = distances.index(min(distances))
    avoidance_box = boxes[i]
    if not box_collision_detection(avoidance_box, coord, 0.45):
        return
    robot.reset_motor_velocities()

    required = required_bearing(avoidance_box, robot.gps.getValues())
    current_bearing = robot.bearing1(robot.compass)

    def angle_between(a, b):
        return min(a - b, a - b + 360, a - b - 360, key=abs)

    error = angle_between(required, current_bearing)

    def rotation_clockwise():
        bearing = required + 60
        if bearing > 180:
            bearing -= 360
        return bearing

    def rotation_anticlockwise():
        bearing = required - 60
        if bearing < -180:
            bearing += 360
        return bearing
    outer_velocity = 4.0
    inner_velocity = 2.0

    if error > 0:
        PID_rotation(rotation_anticlockwise())
        robot.set_motor_velocities(inner_velocity, outer_velocity)

    else:
        PID_rotation(rotation_clockwise())
        robot.set_motor_velocities(outer_velocity, inner_velocity)

    """PID_rotation(rotation_clockwise(current_bearing))
    wall_distance = get_wall_position(robot.bearing(robot.compass), robot.gps.getValues())
    outer_velocity = 3.0
    inner_velocity = 1.5
    print(wall_distance)

    if wall_distance > 0.4:
        robot.set_motor_velocities(inner_velocity, outer_velocity)

    else:
        PID_rotation(rotation_anticlockwise(current_bearing))
        robot.set_motor_velocities(outer_velocity, inner_velocity)"""
    collision = box_collision_detection(avoidance_box, coord, threshold_distance=0.45)
    while collision:
        required = required_bearing(avoidance_box, robot.gps.getValues())
        current_bearing = robot.bearing1(robot.compass)
        error = angle_between(required, current_bearing)
        distance = np.linalg.norm(box - robot.current_location())
        robot.step()
        collision = box_collision_detection(avoidance_box, coord, threshold_distance=0.45)
        print(collision)

    robot.reset_motor_velocities()
    return


def test_pid_rotation():
    PID_rotation(90)
    t = 0
    while t < 1:
        t += robot.step()
    PID_rotation(-45)
    while t < 2:
        t += robot.step()


print('********')
# robot.step(read_sensors=False)
# if robot.colour == 'red':
#    coord = (0.9,0.4)
#    move(coord)


# This part is executed

robot.step(read_sensors=False)


if robot.colour == 'green':
    PID_rotation(180)
else:
    PID_rotation(0)


positions = sweep()

robot.step(read_sensors=False)
robot.send_sweep_locations(positions)
robot.step(read_sensors=False)
initial_pass = True

robot.parked = False


while True:

    while len(robot.box_list) != 0 and robot.field.available():
        robot.parked = False
        robot.send_message('done', 4)

        t = robot.get_next_target()
        robot.current_target = t[1]

        if DEBUG_MAINLOOP:
            robot.update_unique_boxes()
            print(robot.get_unique_boxes(), 'all boxes')

        pos = t[1]
        # print(pos)
        robot.withdraw_dualclaw()

        if initial_pass:
            initial_pass = False
            move(pos)
        else:
            move_avoid_fields(pos, error_translation=0.1)

        robot.step()
        # if this is a new box and colour needs to be checked
        if t[0] == 0:

            # -1, -2 for errors, True for getting same colour, False for detecting different colour
            result = robot.get_target()

            if result == -1:
                print('did not detect box')
            elif result == -2:
                print('failed to detect colour after remeasure')
            elif result:
                robot.move_forwards(-0.10, 0.01)
                return_box_field(robot.gps.getValues())
            else:
                robot.move_forwards(-0.15, 0.02)
                valid, x, z = robot.remeasure_position()
                if valid:
                    robot.send_box_location(np.array([x, z]))

        else:  # this is a known box, got a location form another robot, just need to pick it up
            robot.close_dualclaw()
            if not robot.dsUltrasonic.getValue() > 0.15:
                # check if it is actually holding a box, otherwise just go on looking for the next box
                robot.move_forwards(-0.06, 0.01)
                return_box_field(robot.gps.getValues())
            else:
                robot.withdraw_dualclaw()

    if not robot.parked:
        robot.parked = finish_in_field()
        robot.current_target = []
        robot.send_message('parked', 4)

        # check if that robot finished finding it's boxes
        if not robot.field.available:

            if len(robot.box_list) > 0:
                # send the remaining locations to the other robot
                for _, item in enumerate(robot.box_list):
                    pos = item[1]
                    robot.send_box_location(pos)
            robot.box_list = []

    if((robot.field.available() == True) and robot.sweep_ready == False):
        robot.send_message('available', 8)

    # Yield if parked, otherwise Webots will be stuck waiting for us
    robot.step()

    # Second sweep check
    if((robot.other_parked == True) and (robot.parked == True) and ((robot.field.available() == True) or (robot.other_available == True)) and (robot.sweep_ready == False)):

        robot.parked = False
        positions_second = second_sweep(1.8)

        robot.send_sweep_locations(positions_second)
        robot.second_sweep_locations_ready = True
        robot.send_message('locations sent', type=10)

        while not robot.other_second_sweep_locations_ready:
            robot.step()
        robot.compare_sweep_results()

        if not robot.field.available:

            if len(robot.box_list) > 0:
                # send the remaining locations to the other robot
                for _, item in enumerate(robot.box_list):
                    pos = item[1]
                    robot.send_box_location(pos)
            robot.box_list = []

        # print(robot.box_list)
